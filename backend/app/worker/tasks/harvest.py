"""采集 Celery 任务（W3 采集流水线 + W6 多源扩展）。

核心逻辑在 app/services（无 celery 依赖，测试离线可跑）；
本模块只做 worker 装配：出网传输 + 会话 + 存储目录。

W6 变更：
- 采集客户端不再硬编码 USGS，改为按任务所属 data_source.code 走适配器注册表
  （usgs / gbif / esa 三源）；W2–W5 沿用的 `harvest.run_usgs` 任务名保留为别名，
  存量调度配置不需要改。
- 新增 `harvest.dispatch_round`：按调度策略（cron + 限流时段 + 配额前置）
  挑出到期任务并行拉取，作为 beat 周期入口。
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

from ...config import settings
from ...db import SessionLocal
from ...models import DataSource, DatasetCategory, HarvestTask
from ...services.dispatch_service import run_dispatch_round
from ...services.pipeline import run_collect_pipeline
from ...services.sources import build_adapter
from ...services.storage import LocalBlobStore
from ...services.usgs import HarvestItem


class HTTPXTransport:
    """httpx 传输实现：worker 侧注入各源适配器（三源共用同一传输契约）。"""

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


def adapter_for(code: str, transport=None, *, base_url: str | None = None):
    """按源编码构造适配器；base_url 取自库内 data_source 配置（可为空回落默认）。"""
    kwargs = {}
    if base_url:
        kwargs["base_url"] = base_url
    return build_adapter(code, transport or HTTPXTransport(), **kwargs)


def _run_source_harvest(task_id: int, max_items: int = 10) -> dict:
    """任务实现：按任务所属数据源选择适配器，采集→落盘→检测。

    采集成功则继续落盘+安全检测（失败已由服务层分类落库）。
    """
    db = SessionLocal()
    try:
        task = db.get(HarvestTask, task_id)
        if task is None:
            return {"task_id": task_id, "outcome": "missing", "items": 0}
        source = db.get(DataSource, task.data_source_id)
        code = source.code if source else "usgs"
        client = adapter_for(code, base_url=(source.base_url if source else None))

        category = (
            db.query(DatasetCategory)
            .filter(DatasetCategory.code == settings.default_category_code)
            .one_or_none()
        )
        if category is None:
            # 分类字典未配置：只采集不落盘（不阻断采集调度）
            from ...services.harvest_service import run_harvest_once

            result = run_harvest_once(db, task_id, client, max_items=max_items)
            return {
                "task_id": result.task_id,
                "source": code,
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
            "source": code,
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


# W6 正式入口：按源编码自动选适配器
run_source_harvest = shared_task(name="harvest.run_source")(_run_source_harvest)
# W2–W5 任务名别名：存量 beat / 手工触发配置无需改动（同一实现，两个注册名）
run_usgs_harvest = shared_task(name="harvest.run_usgs")(_run_source_harvest)


@shared_task(name="harvest.dispatch_round")
def dispatch_round(max_items: int = 10) -> dict:
    """beat 周期入口：按调度策略挑到期任务，3 源并行拉取。"""
    db = SessionLocal()
    try:
        result = run_dispatch_round(
            db,
            adapter_for,
            max_items=max_items,
        )
        return {
            "considered": result.considered,
            "dispatched": result.dispatched,
            "succeeded": result.succeeded,
            "retry_scheduled": result.retry_scheduled,
            "failed": result.failed,
            "skipped": result.skipped,
            "failure_classes": result.failure_classes,
        }
    finally:
        db.close()
