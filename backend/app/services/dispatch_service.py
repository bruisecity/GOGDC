"""采集调度服务（W6：3 源（USGS / GBIF / ESA）并行采集 + 调度策略完善）。

职责边界：
- 到期判定走 `cron_policy.is_due`（cron 表达式 + 限流时段）；
- **配额前置检查**（设计稿第三章：quota_limit / quota_used「采集调度前置检查」）：
  配额已满（quota_limit > 0 且 quota_used >= quota_limit）→ 本轮跳过并落审计，
  不向源站发请求；
- **并行拉取**：各源互不阻塞（单源故障不放大成串行等待），并发上限由
  `max_workers` 控制；线程只做出网 IO，**不碰 DB 会话**——落地在拉取返回后
  串行完成，避免 SQLAlchemy Session 跨线程使用；
- **健康度**：成功 → healthy + last_success_at；可重试失败 → degraded；
  不可重试失败 / 重试耗尽 → down；
- **失败率按四类原因计数**：每轮结束落一条「采集调度」审计，
  detail.failure_classes 为四类各自计数（缺省 0），供监控按类统计。

口径说明：调度器不负责落盘（ingest）与检测，那是 worker 流水线的职责；
本服务给出「该跑哪些任务、各自结果如何」，供 worker / 控制台复用。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from ..models import AuditLog, DataSource, HarvestTask
from .cron_policy import CronPolicyError, is_due, normalized_last_run_at, utc_now
from .harvest_service import HarvestRunResult, apply_harvest_outcome
from .sources import ACTIVE_SOURCES
from .usgs import FAILURE_CLASSES, HarvestError, HarvestItem


@dataclass
class DispatchOutcome:
    """单个任务的调度结果。"""

    task_id: int
    source_code: str
    action: str           # "skipped" | "dispatch_failed" | HarvestRunResult.outcome
    items: int = 0
    failure_class: str | None = None
    reason: str = ""
    fetched: list = field(default_factory=list)


@dataclass
class DispatchResult:
    """一轮调度的汇总。"""

    considered: int = 0
    dispatched: int = 0
    succeeded: int = 0
    retry_scheduled: int = 0
    failed: int = 0
    skipped: int = 0
    outcomes: list[DispatchOutcome] = field(default_factory=list)
    failure_classes: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.failure_classes:
            # 四类失败恒有键，便于监控直接取数（缺省 0 而非缺键）
            self.failure_classes = {c: 0 for c in FAILURE_CLASSES}

    def count(self, klass: str) -> int:
        return self.failure_classes.get(klass, 0)

    @property
    def fetched_by_task(self) -> dict[int, list]:
        return {o.task_id: o.fetched for o in self.outcomes if o.fetched}


def quota_exhausted(source: DataSource) -> bool:
    """配额前置检查：上限为 0 视为不限量。"""
    limit = source.quota_limit or 0
    used = source.quota_used or 0
    return limit > 0 and used >= limit


def record_health(
    source: DataSource, result: HarvestRunResult, *, now: datetime | None = None
) -> str:
    """按一次执行结果推进数据源健康度，返回新健康度。"""
    stamp = now or utc_now()
    if result.outcome == "succeeded":
        source.health_status = "healthy"
        source.last_success_at = stamp
    elif result.outcome == "retry_scheduled":
        # 仍可重试：降级但不判死（源站限流/超时属于可恢复态）
        source.health_status = "degraded"
    else:
        source.health_status = "down"
    return source.health_status


def due_tasks(db: Session, *, now: datetime | None = None) -> list[HarvestTask]:
    """筛出本轮到期的采集任务：状态 idle、属在役源、cron 到点、在限流时段内。"""
    stamp = now or utc_now()
    rows = (
        db.query(HarvestTask)
        .filter(HarvestTask.status == "idle")
        .order_by(HarvestTask.id)
        .all()
    )
    out: list[HarvestTask] = []
    for task in rows:
        source = db.get(DataSource, task.data_source_id)
        if source is None or source.code not in ACTIVE_SOURCES:
            continue
        try:
            if is_due(
                task.schedule_cron,
                normalized_last_run_at(task.last_run_at),
                stamp,
                time_window=task.time_window or "",
            ):
                out.append(task)
        except CronPolicyError:
            # 表达式写错不应拖垮整轮调度：跳过该任务，由控制台修正后自然恢复
            continue
    return out


def _fetch_one(
    adapter_factory: Callable[[str], object], source_code: str, max_items: int
) -> tuple[list[HarvestItem] | None, HarvestError | None]:
    """线程内只做出网拉取，不触碰 DB。"""
    try:
        adapter = adapter_factory(source_code)
        return list(adapter.fetch_items(max_items=max_items)), None
    except HarvestError as exc:
        return None, exc
    except Exception as exc:  # 适配器以外的基础设施异常同样归类为连接超时
        return None, HarvestError(
            "连接超时", f"{source_code} 拉取异常: {type(exc).__name__}: {exc}", retryable=True
        )


def run_dispatch_round(
    db: Session,
    adapter_factory: Callable[[str], object],
    *,
    max_items: int = 10,
    max_workers: int = 4,
    now: datetime | None = None,
    user_id: int | None = None,
    trace_id: str | None = None,
    task_ids: Iterable[int] | None = None,
) -> DispatchResult:
    """执行一轮调度：到期任务并行拉取 → 串行落地状态机 → 汇总审计。

    `adapter_factory(code)` 由调用方装配（生产=HTTPXTransport + build_adapter，
    测试=假 transport）。`task_ids` 给定则只跑这些任务（控制台手动触发口径，
    仍走同一状态语义）：此时忽略 cron 到期判定，但配额前置检查与限流时段仍生效。
    """
    stamp = now or utc_now()
    result = DispatchResult()

    if task_ids is None:
        targets = due_tasks(db, now=stamp)
    else:
        targets = []
        for tid in task_ids:
            task = db.get(HarvestTask, tid)
            if task is None or task.status != "idle":
                continue
            source = db.get(DataSource, task.data_source_id)
            if source is None or source.code not in ACTIVE_SOURCES:
                continue
            targets.append(task)

    result.considered = len(targets)

    # 1) 配额前置检查：配额已满的任务不向源站发请求
    runnable: list[HarvestTask] = []
    for task in targets:
        source = db.get(DataSource, task.data_source_id)
        if quota_exhausted(source):
            result.skipped += 1
            result.outcomes.append(
                DispatchOutcome(
                    task_id=task.id,
                    source_code=source.code,
                    action="skipped",
                    reason=f"配额已满（used={source.quota_used}/limit={source.quota_limit}）",
                )
            )
            db.add(
                AuditLog(
                    user_id=user_id,
                    operation="采集调度",
                    resource="harvest",
                    result="跳过",
                    detail={
                        "task_id": task.id,
                        "source": source.code,
                        "reason": "quota_exhausted",
                        "quota_used": source.quota_used or 0,
                        "quota_limit": source.quota_limit or 0,
                    },
                    trace_id=trace_id,
                )
            )
            continue
        runnable.append(task)
    db.commit()

    # 2) 先占位 running 再拉取：同任务重复调度时状态机只允许一条路径推进
    claimed: list[HarvestTask] = []
    for task in runnable:
        source = db.get(DataSource, task.data_source_id)
        try:
            task.status = "running"
            db.add(task)
            db.commit()
            claimed.append(task)
        except Exception:  # pragma: no cover —— 提交失败：回滚并跳过该任务
            db.rollback()
            result.skipped += 1
            result.outcomes.append(
                DispatchOutcome(
                    task_id=task.id,
                    source_code=source.code,
                    action="skipped",
                    reason="状态占位失败",
                )
            )

    if not claimed:
        _audit_round(db, result, user_id=user_id, trace_id=trace_id)
        return result

    # 3) 并行拉取（每任务一个 worker 任务；源站之间互不阻塞）
    pairs = {
        task.id: db.get(DataSource, task.data_source_id).code for task in claimed
    }
    fetched: dict[int, list[HarvestItem] | None] = {}
    errors: dict[int, HarvestError | None] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(claimed)))) as pool:
        futures = {
            pool.submit(_fetch_one, adapter_factory, code, max_items): tid
            for tid, code in pairs.items()
        }
        for future in futures:
            tid = futures[future]
            items, error = future.result()
            fetched[tid] = items
            errors[tid] = error

    # 4) 串行落地状态机（单一会话，事务语义清晰）
    for tid, code in pairs.items():
        task = db.get(HarvestTask, tid)
        source = db.get(DataSource, task.data_source_id)
        run_result = apply_harvest_outcome(
            db, tid, items=fetched.get(tid) if errors.get(tid) is None else None,
            error=errors.get(tid),
        )
        record_health(source, run_result, now=stamp)
        task.last_run_at = stamp
        db.add(source)
        db.add(task)
        db.commit()

        result.dispatched += 1
        if run_result.outcome == "succeeded":
            result.succeeded += 1
        elif run_result.outcome == "retry_scheduled":
            result.retry_scheduled += 1
        else:
            result.failed += 1
        if run_result.failure_class:
            result.failure_classes[run_result.failure_class] = (
                result.failure_classes.get(run_result.failure_class, 0) + 1
            )
        result.outcomes.append(
            DispatchOutcome(
                task_id=tid,
                source_code=code,
                action=run_result.outcome,
                items=run_result.items,
                failure_class=run_result.failure_class,
                fetched=run_result.fetched,
            )
        )

    _audit_round(db, result, user_id=user_id, trace_id=trace_id)
    return result


def _audit_round(
    db: Session, result: DispatchResult, *, user_id: int | None, trace_id: str | None
) -> None:
    """每轮一条汇总审计：四类失败计数 + 各任务结果（监控按类统计的口径）。"""
    db.add(
        AuditLog(
            user_id=user_id,
            operation="采集调度",
            resource="harvest",
            result="成功" if result.failed == 0 else "部分失败",
            detail={
                "considered": result.considered,
                "dispatched": result.dispatched,
                "succeeded": result.succeeded,
                "retry_scheduled": result.retry_scheduled,
                "failed": result.failed,
                "skipped": result.skipped,
                "failure_classes": dict(result.failure_classes),
                "tasks": [
                    {
                        "task_id": o.task_id,
                        "source": o.source_code,
                        "action": o.action,
                        "items": o.items,
                        "failure_class": o.failure_class,
                        "reason": o.reason,
                    }
                    for o in result.outcomes
                ],
            },
            trace_id=trace_id,
        )
    )
    db.commit()
