"""USGS 采集 Celery 任务（W2：单源采集调度入口）。

采集核心逻辑在 app/services/harvest_service（无 celery 依赖，
测试离线可跑）；本模块只做 worker 装配。未安装 celery 时
导入报错并给出安装提示（与 celery_app.py 同口径）。
"""
from __future__ import annotations

try:
    from celery import shared_task
except ImportError as exc:  # pragma: no cover —— 环境缺依赖时的显式降级
    raise ImportError(
        "celery 未安装：worker 需先 `pip install celery>=5.3 redis>=5.2`。"
        "测试与 API 层不依赖本模块，可直接运行。"
    ) from exc

import httpx

from ..db import SessionLocal
from ..services.harvest_service import run_harvest_once
from ..services.usgs import USGSTNMClient


class HTTPXTransport:
    """httpx 传输实现：worker 侧注入 USGSTNMClient。"""

    def request(self, method: str, url: str, params: dict):
        resp = httpx.request(method, url, params=params, timeout=30.0)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, None


@shared_task(name="harvest.run_usgs")
def run_usgs_harvest(task_id: int, max_items: int = 10) -> dict:
    """Celery 入口：执行一次 USGS 采集（失败已由服务层分类落库）。"""
    client = USGSTNMClient(HTTPXTransport())
    db = SessionLocal()
    try:
        result = run_harvest_once(db, task_id, client, max_items=max_items)
        return {
            "task_id": result.task_id,
            "outcome": result.outcome,
            "items": result.items,
            "retry_count": result.retry_count,
            "failure_class": result.failure_class,
        }
    finally:
        db.close()
