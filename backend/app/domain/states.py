"""领域状态机：采集任务 / 隔离样本处置 / 编目发布。

依据《Geodata（GOGDC）平台轻量技术设计框架与垂直切片排期》第三章字段级设计：
- harvest_task.status: idle / running / failed / paused
- quarantine_sample.disposal_state: 待处置 → 人工复核 → 执行处置 → 二次确认 → 归档
- catalog_batch.publish_state: 草稿 → 字段校验 → 预览 → 发布 → 归档

纯逻辑、无框架依赖，便于单测与复用。
"""
from __future__ import annotations

# 采集任务状态机（DB 存英文码，展示层映射中文）
HARVEST_TRANSITIONS: dict[str, set[str]] = {
    "idle": {"running", "paused"},
    "running": {"idle", "failed", "paused"},
    "failed": {"idle", "paused"},          # 重试由 retry_count 驱动，回到 idle
    "paused": {"idle"},                     # 恢复即重新排队
}

# 隔离样本处置五步状态机（中文枚举与设计稿一致）
DISPOSAL_STEPS: list[str] = ["待处置", "人工复核", "执行处置", "二次确认", "归档"]

# 编目发布五步状态机
PUBLISH_STEPS: list[str] = ["草稿", "字段校验", "预览", "发布", "归档"]


class InvalidTransition(ValueError):
    """非法状态流转。code 原样写回 API 错误信封。"""

    def __init__(self, entity: str, current: str, target: str):
        self.entity, self.current, self.target = entity, current, target
        super().__init__(f"{entity}: {current!r} -> {target!r} 不允许")


class BatchValidationError(ValueError):
    """编目批次字段校验未通过（422 BATCH_VALIDATION_FAILED）。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def harvest_transition(current: str, target: str) -> str:
    """校验采集任务状态流转，返回目标状态。"""
    if current not in HARVEST_TRANSITIONS:
        raise InvalidTransition("harvest_task", current, target)
    if target not in HARVEST_TRANSITIONS[current]:
        raise InvalidTransition("harvest_task", current, target)
    return target


def step_forward(steps: list[str], current: str, target: str) -> str:
    """线性五步状态机：只允许停在当前步或前进到后续步（不可跳步、不可回退）。"""
    if current not in steps or target not in steps:
        raise InvalidTransition("linear_state", current, target)
    if steps.index(target) - steps.index(current) not in (0, 1):
        # 不可回退、不可跳步（如 待处置 -> 归档）
        raise InvalidTransition("linear_state", current, target)
    return target


def disposal_transition(current: str, target: str) -> str:
    """隔离样本处置流转。"""
    return step_forward(DISPOSAL_STEPS, current, target)


def publish_transition(current: str, target: str) -> str:
    """编目批次发布流转。"""
    return step_forward(PUBLISH_STEPS, current, target)
