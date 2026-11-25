"""存储分层周期任务（W5：生命周期迁移 + 水位巡检）。

核心逻辑在 app/services（无 celery 依赖，测试离线可跑）；
本模块只做 worker 装配。可经 celery beat 周期调度：
- storage.lifecycle_pass 每日 02:30 执行一轮热/温/冷迁移；
- storage.watermark_check 每小时巡检容量水位并落告警/恢复审计。
"""
from __future__ import annotations

try:
    from celery import shared_task
except ImportError as exc:  # pragma: no cover —— 环境缺依赖时的显式降级
    raise ImportError(
        "celery 未安装：worker 需先 `pip install celery>=5.3 redis>=5.2`。"
        "测试与 API 层不依赖本模块，可直接运行。"
    ) from exc

from ...db import SessionLocal
from ...services.lifecycle_service import run_lifecycle_pass
from ...services.tiering import default_tier_stores
from ...services.watermark_service import check_watermarks


@shared_task(name="storage.lifecycle_pass")
def lifecycle_pass() -> dict:
    """执行一轮生命周期迁移（策略在桶上配置，见 docs/W5-STORAGE-LIFECYCLE.md）。"""
    db = SessionLocal()
    try:
        result = run_lifecycle_pass(db, default_tier_stores())
        return {
            "migrated": result.migrated,
            "transitions": result.transitions,
            "policy_failures": result.policy_failures,
        }
    finally:
        db.close()


@shared_task(name="storage.watermark_check")
def watermark_check() -> dict:
    """巡检全部桶水位：越线落「告警」、回落落「恢复」审计（幂等去重）。"""
    db = SessionLocal()
    try:
        alerts = check_watermarks(db)
        return {"alerts": alerts}
    finally:
        db.close()
