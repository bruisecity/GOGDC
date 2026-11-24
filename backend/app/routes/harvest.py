"""采集任务控制台（设计稿 §7.2）。

GET/POST /api/console/harvest-tasks；PATCH /{id} —— 状态流转走
domain/states.harvest_transition 状态机，非法流转返回 422 信封。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from ..core.auth import PrincipalDep, require
from ..core.errors import NotFoundError
from ..domain.states import harvest_transition
from ..models import DataSource, HarvestTask, Node
from ..schema import HarvestTaskCreate, HarvestTaskListOut, HarvestTaskOut, HarvestTaskPatch
from .deps import SessionDep

router = APIRouter(prefix="/api/console/harvest-tasks", tags=["harvest"])


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
    return HarvestTaskListOut(
        items=[HarvestTaskOut.model_validate(r) for r in rows], total=total
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(payload: HarvestTaskCreate, principal: PrincipalDep, db: SessionDep):
    require("harvest", "写")(principal)
    if db.get(DataSource, payload.data_source_id) is None:
        raise NotFoundError(f"数据源 {payload.data_source_id} 不存在")
    if db.get(Node, payload.node_id) is None:
        raise NotFoundError(f"汇聚节点 {payload.node_id} 不存在")
    task = HarvestTask(
        data_source_id=payload.data_source_id,
        node_id=payload.node_id,
        schedule_cron=payload.schedule_cron,
        retry_max=payload.retry_max,
        status="idle",
        retry_count=0,
    )
    db.add(task)
    db.commit()
    return HarvestTaskOut.model_validate(task)


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
    return HarvestTaskOut.model_validate(task)
