"""采集任务控制台（设计稿 §7.2 + W6 调度策略完善）。

- GET/POST /api/console/harvest-tasks；PATCH /{id} —— 状态流转走
  domain/states.harvest_transition 状态机，非法流转返回 422 信封。
- POST /api/console/harvest-tasks/dispatch/run —— 手动触发一轮调度
  （不传 task_ids 则按 cron + 限流时段挑到期任务）。
- GET  /api/console/harvest-tasks/dispatch/status —— 在役源健康度 + 到期任务概览。

W6：创建任务时校验 cron 表达式与限流时段格式，避免脏配置进库后
调度器每轮静默跳过。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from ..core.auth import PrincipalDep, require
from ..core.errors import NotFoundError
from ..domain.states import harvest_transition
from ..models import DataSource, HarvestTask, Node
from ..schema import (
    DispatchOut,
    DispatchRunIn,
    DispatchTaskOut,
    HarvestTaskCreate,
    HarvestTaskListOut,
    HarvestTaskOut,
    HarvestTaskPatch,
)
from ..services.cron_policy import CronPolicyError, parse_cron, parse_time_window
from ..services.dispatch_service import (
    due_tasks,
    run_dispatch_round,
)
from ..services.sources import ACTIVE_SOURCES, build_adapter, ADAPTERS
from .deps import SessionDep

router = APIRouter(prefix="/api/console/harvest-tasks", tags=["harvest"])


def _out(task: HarvestTask) -> HarvestTaskOut:
    return HarvestTaskOut.model_validate(task)


@router.get("")
def list_tasks(
    principal: PrincipalDep,
    db: SessionDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    require("harvest", "读")(principal)
    total = db.query(HarvestTask).count()
    rows = (
        db.query(HarvestTask)
        .order_by(HarvestTask.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return HarvestTaskListOut(items=[_out(r) for r in rows], total=total)


def _validate_policy(schedule_cron: str, time_window: str) -> None:
    """创建/更新时前置校验调度策略，非法即 422（不让脏配置进库）。"""
    if schedule_cron:
        try:
            parse_cron(schedule_cron)
        except CronPolicyError as exc:
            raise HTTPException(status_code=422, detail=f"schedule_cron 非法: {exc}") from exc
    try:
        parse_time_window(time_window)
    except CronPolicyError as exc:
        raise HTTPException(status_code=422, detail=f"time_window 非法: {exc}") from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(payload: HarvestTaskCreate, principal: PrincipalDep, db: SessionDep):
    require("harvest", "写")(principal)
    if db.get(DataSource, payload.data_source_id) is None:
        raise NotFoundError(f"数据源 {payload.data_source_id} 不存在")
    if db.get(Node, payload.node_id) is None:
        raise NotFoundError(f"汇聚节点 {payload.node_id} 不存在")
    _validate_policy(payload.schedule_cron, payload.time_window)
    task = HarvestTask(
        data_source_id=payload.data_source_id,
        node_id=payload.node_id,
        schedule_cron=payload.schedule_cron,
        time_window=payload.time_window,
        retry_max=payload.retry_max,
        status="idle",
        retry_count=0,
    )
    db.add(task)
    db.commit()
    return _out(task)


@router.patch("/{task_id}")
def patch_task(task_id: int, payload: HarvestTaskPatch, principal: PrincipalDep, db: SessionDep):
    require("harvest", "写")(principal)
    task = db.get(HarvestTask, task_id)
    if task is None:
        raise NotFoundError(f"采集任务 {task_id} 不存在")
    task.status = harvest_transition(task.status, payload.status)
    if payload.status == "running":
        # 手动拉起视为一次执行：清零失败标记口径留给 worker，此处仅状态机
        pass
    db.add(task)
    db.commit()
    return _out(task)


@router.post("/dispatch/run")
def dispatch_run(payload: DispatchRunIn, principal: PrincipalDep, db: SessionDep):
    """手动触发一轮调度（控制台「立即跑一轮」口径，仍走同一状态语义）。"""
    require("harvest", "写")(principal)

    def adapter_factory(code: str):
        source = (
            db.query(DataSource).filter(DataSource.code == code).one_or_none()
        )
        base = source.base_url if source else None
        from ..worker.tasks.harvest import HTTPXTransport

        return build_adapter(code, HTTPXTransport(), **({"base_url": base} if base else {}))

    try:
        result = run_dispatch_round(
            db,
            adapter_factory,
            max_items=payload.max_items,
            user_id=principal.user.id,
            task_ids=payload.task_ids,
        )
    except Exception as exc:  # 适配器未注册 / 传输装配失败等基础设施错误
        raise HTTPException(status_code=503, detail=f"调度执行失败: {exc}") from exc
    return DispatchOut(
        considered=result.considered,
        dispatched=result.dispatched,
        succeeded=result.succeeded,
        retry_scheduled=result.retry_scheduled,
        failed=result.failed,
        skipped=result.skipped,
        failure_classes=result.failure_classes,
        tasks=[
            DispatchTaskOut(
                task_id=o.task_id,
                source=o.source_code,
                action=o.action,
                items=o.items,
                failure_class=o.failure_class,
                reason=o.reason,
            )
            for o in result.outcomes
        ],
    )


@router.get("/dispatch/status")
def dispatch_status(principal: PrincipalDep, db: SessionDep):
    """调度现状：在役源清单 + 健康度 + 本刻到期任务（运维排障口径）。"""
    require("harvest", "读")(principal)
    sources = (
        db.query(DataSource).filter(DataSource.code.in_(ACTIVE_SOURCES)).all()
    )
    due = due_tasks(db)
    return {
        "active_sources": list(ACTIVE_SOURCES),
        "registered_adapters": sorted(ADAPTERS),
        "sources": [
            {
                "code": s.code,
                "health_status": s.health_status,
                "last_success_at": s.last_success_at,
                "quota_limit": s.quota_limit or 0,
                "quota_used": s.quota_used or 0,
                "quota_exhausted": bool(s.quota_limit) and (s.quota_used or 0) >= s.quota_limit,
            }
            for s in sources
        ],
        "due_task_ids": [t.id for t in due],
        "due_count": len(due),
    }
