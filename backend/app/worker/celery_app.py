"""Celery worker 入口（异步链路：采集 → 检测 → 编目 → 存储）。

W1 仅装配骨架（broker/backend 指向 Redis）；任务函数随切片 0（W2–W3）落地：
- harvest_task: USGS 单源拉取（W2）
- detection_task: 四类规则流水线（W3）
- catalog_task: pgSTAC 入库（W3）
注意：celery 依赖在 W2 引入 requirements；本文件不参与 W1 测试导入。
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
    include=[],  # W2: ["app.worker.tasks.harvest"]
)
celery_app.conf.task_default_queue = "geodata.default"
