"""采集执行服务：一次 run 的完整状态机闭环（可调度、可重试）。

流程（设计稿 §6 采集数据流 + §8 W2 验收）：
idle → running → (成功) idle / (失败且可重试未耗尽) idle + retry_count+1 /
(失败不可重试或重试耗尽) failed；last_failure_class 记录四类失败分类。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..domain.states import harvest_transition
from ..models import HarvestTask
from .usgs import HarvestError, USGSTNMClient


@dataclass
class HarvestRunResult:
    task_id: int
    outcome: str          # "succeeded" | "retry_scheduled" | "failed"
    items: int
    retry_count: int
    failure_class: str | None


def run_harvest_once(
    db: Session, task_id: int, client: USGSTNMClient, *, max_items: int = 10
) -> HarvestRunResult:
    """执行一次采集。调用方负责事务提交语义（本函数内 commit）。"""
    task = db.get(HarvestTask, task_id)
    if task is None:
        raise ValueError(f"采集任务不存在: {task_id}")

    task.status = harvest_transition(task.status, "running")
    db.add(task)
    db.commit()

    try:
        items = client.fetch_products(max_items=max_items)
    except HarvestError as exc:
        task.last_failure_class = exc.failure_class
        if exc.retryable and task.retry_count < task.retry_max:
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
            failure_class=exc.failure_class,
        )

    # 成功：清零重试口径，回 idle（等待下一次调度）
    task.retry_count = 0
    task.last_failure_class = None
    task.status = harvest_transition("running", "idle")
    db.add(task)
    db.commit()
    return HarvestRunResult(
        task_id=task.id,
        outcome="succeeded",
        items=len(items),
        retry_count=0,
        failure_class=None,
    )
