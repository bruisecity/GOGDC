"""Celery worker 入口（异步链路：采集 → 检测 → 编目 → 存储）。

任务模块：
- harvest（W2–W3）：USGS 单源拉取 + 落盘 + 检测流水线
- storage（W5）：生命周期迁移（storage.lifecycle_pass）+ 水位巡检（storage.watermark_check）

周期调度：celery -A app.worker.celery_app.celery_app beat
"""
from __future__ import annotations

try:
    from celery import Celery
except ImportError as e:  # W1 骨架容忍未安装
    raise ImportError(
        "celery 未安装（W2 引入）：pip install celery>=5.3"
    ) from e

from app.config import settings

celery_app = Celery(
    "geodata",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.worker.tasks.harvest",   # W2: USGS 单源采集任务
        "app.worker.tasks.storage",  # W5: 生命周期迁移 + 水位巡检
    ],
)
celery_app.conf.task_default_queue = "geodata.default"
# W5：周期调度（celery beat）
celery_app.conf.beat_schedule = {
    "storage-watermark-hourly": {
        "task": "storage.watermark_check",
        "schedule": 3600.0,  # 每小时巡检容量水位
    },
    "storage-lifecycle-daily": {
        "task": "storage.lifecycle_pass",
        "schedule": 3600.0 * 24,  # 每日一轮热/温/冷迁移
    },
}
