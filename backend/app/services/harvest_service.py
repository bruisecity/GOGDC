"""采集执行服务：一次 run 的完整状态机闭环（可调度、可重试）。

W6：客户端形态不再限定 USGSTNMClient——任何满足 `fetch_items(max_items)`
的源适配器（见 app/services/sources）都可用；W2–W5 的 USGSTNMClient
（`fetch_products`）保持受支持，由本模块统一分派到对应方法。

流程（设计稿 §6 采集数据流 + §8 W2 验收）：
idle → running → (成功) idle / (失败且可重试未耗尽) idle + retry_count+1 /
(失败不可重试或重试耗尽) failed；last_failure_class 记录四类失败分类。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..domain.states import harvest_transition
from ..models import HarvestTask
from .usgs import HarvestError


@dataclass
class HarvestRunResult:
    task_id: int
    outcome: str          # "succeeded" | "retry_scheduled" | "failed"
    items: int
    retry_count: int
    failure_class: str | None
    fetched: list = None   # W3：成功时携带拉到的条目（HarvestItem 列表）

    def __post_init__(self) -> None:
        if self.fetched is None:
            self.fetched = []


def apply_harvest_outcome(
    db: Session,
    task_id: int,
    *,
    items: list | None = None,
    error: HarvestError | None = None,
) -> HarvestRunResult:
    """把一次拉取的结果落库到状态机（W6：供调度器在并行拉取后串行落地）。

    前置条件：task.status 已是 running（调用方先占位再拉取，避免重复调度）。
    成功或失败二选一传入；本函数内 commit。
    """
    task = db.get(HarvestTask, task_id)
    if task is None:
        raise ValueError(f"采集任务不存在: {task_id}")

    if error is not None:
        task.last_failure_class = error.failure_class
        if error.retryable and task.retry_count < task.retry_max:
            # 可重试且未耗尽：回 idle 等待下一轮调度，计数 +1
            task.retry_count += 1
            task.status = harvest_transition("running", "idle")
            outcome = "retry_scheduled"
        else:
            task.status = harvest_transition("running", "failed")
            outcome = "failed"
        db.add(task)
        db.commit()
        return HarvestRunResult(
            task_id=task.id,
            outcome=outcome,
            items=0,
            retry_count=task.retry_count,
            failure_class=error.failure_class,
        )

    fetched = list(items or [])
    # 成功：清零重试口径，回 idle（等待下一次调度）
    task.retry_count = 0
    task.last_failure_class = None
    task.status = harvest_transition("running", "idle")
    db.add(task)
    db.commit()
    return HarvestRunResult(
        task_id=task.id,
        outcome="succeeded",
        items=len(fetched),
        retry_count=0,
        failure_class=None,
        fetched=fetched,
    )


def _fetch_items(client, max_items: int) -> list:
    """统一分派：W6 适配器走 fetch_items，W2–W5 客户端走 fetch_products。"""
    fetch_items = getattr(client, "fetch_items", None)
    if callable(fetch_items):
        return list(fetch_items(max_items=max_items))
    return list(client.fetch_products(max_items=max_items))


def run_harvest_once(
    db: Session, task_id: int, client, *, max_items: int = 10
) -> HarvestRunResult:
    """执行一次采集。调用方负责事务提交语义（本函数内 commit）。

    W6 起复用 apply_harvest_outcome：状态推进逻辑只此一处，
    调度器并行拉取后的落地与单源直连走同一状态语义。
    """
    task = db.get(HarvestTask, task_id)
    if task is None:
        raise ValueError(f"采集任务不存在: {task_id}")

    task.status = harvest_transition(task.status, "running")
    db.add(task)
    db.commit()

    try:
        items = _fetch_items(client, max_items)
    except HarvestError as exc:
        return apply_harvest_outcome(db, task_id, error=exc)

    return apply_harvest_outcome(db, task_id, items=list(items))
