"""USGS 采集 Celery 任务（W3：采集→落盘→检测 全流水线）。

核心逻辑在 app/services/pipeline（无 celery 依赖，测试离线可跑）；
本模块只做 worker 装配：出网下载 payload + 会话 + 存储目录。
未安装 celery 时导入报错并给出安装提示（与 celery_app.py 同口径）。
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

from ..config import settings
from ..db import SessionLocal
from ..models import DatasetCategory
from ..services.pipeline import run_collect_pipeline
from ..services.storage import LocalBlobStore
from ..services.usgs import USGSTNMClient, HarvestItem


class HTTPXTransport:
    """httpx 传输实现：worker 侧注入 USGSTNMClient。"""

    def request(self, method: str, url: str, params: dict):
        resp = httpx.request(method, url, params=params, timeout=30.0)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, None


def download_payload(item: HarvestItem) -> bytes:
    """worker 侧出网下载条目内容（生产路径；测试用合成字节替代）。"""
    resp = httpx.get(item.url, timeout=120.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


@shared_task(name="harvest.run_usgs")
def run_usgs_harvest(task_id: int, max_items: int = 10) -> dict:
    """Celery 入口：采集成功则继续落盘+安全检测（失败已由服务层分类落库）。"""
    client = USGSTNMClient(HTTPXTransport())
    db = SessionLocal()
    try:
        category = (
            db.query(DatasetCategory)
            .filter(DatasetCategory.code == settings.default_category_code)
            .one_or_none()
        )
        if category is None:
            # 字典未配置分类：只采集不落盘（记录失败分类，不阻断采集调度）
            from ..services.harvest_service import run_harvest_once

            result = run_harvest_once(db, task_id, client, max_items=max_items)
            return {
                "task_id": result.task_id,
                "outcome": result.outcome,
                "items": result.items,
                "retry_count": result.retry_count,
                "failure_class": result.failure_class,
                "ingested": 0,
                "warning": f"dataset_category {settings.default_category_code} 未配置，跳过落盘",
            }

        pipeline = run_collect_pipeline(
            db,
            task_id,
            client,
            LocalBlobStore(settings.storage_dir),
            download_payload,
            category_id=category.id,
            bucket_name=settings.s3_bucket,
            max_items=max_items,
        )
        return {
            "task_id": pipeline.harvest.task_id,
            "outcome": pipeline.harvest.outcome,
            "items": pipeline.harvest.items,
            "retry_count": pipeline.harvest.retry_count,
            "failure_class": pipeline.harvest.failure_class,
            "ingested": pipeline.ingest.ingested if pipeline.ingest else 0,
            "skipped": pipeline.ingest.skipped if pipeline.ingest else 0,
            "detection": [d.status for d in pipeline.detection],
        }
    finally:
        db.close()
