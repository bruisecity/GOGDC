"""采集→落盘→检测 流水线（W3 切片 5：一次任务跑通前半程数据流）。

数据流（设计稿 §6）：采集调度 → 汇聚节点落地（ingest_service）
→ 安全检测（detection_service）→ STAC 编目（catalog_service，控制台人工触发）。
编目批次由操作员在控制台创建/推进，不并入自动流水线。

payload_of 回调提供条目字节内容：生产=worker 经出网传输下载，
测试=合成字节（沙箱/CI 不依赖 USGS 可达性）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from ..models import DataSource, HarvestTask
from .detection_service import DetectionResult, run_detection
from .harvest_service import HarvestRunResult, run_harvest_once
from .ingest_service import IngestSummary, ingest_harvest_items
from .storage import BlobStore


@dataclass
class PipelineResult:
    """一次流水线的汇总（采集结果 + 落盘 + 检测明细）。"""

    harvest: HarvestRunResult
    ingest: IngestSummary | None = None
    detection: list[DetectionResult] = field(default_factory=list)


def run_collect_pipeline(
    db: Session,
    task_id: int,
    client: object,          # W6：SourceAdapter 或 W2–W5 的 USGSTNMClient
    blob_store: BlobStore,
    payload_of: Callable,
    *,
    category_id: int,
    bucket_name: str,
    max_items: int = 10,
    trace_id: str | None = None,
    user_id: int | None = None,
) -> PipelineResult:
    """采集成功才继续落盘+检测；失败（含重试调度）直接返回采集结果。"""
    harvest = run_harvest_once(db, task_id, client, max_items=max_items)
    if harvest.outcome != "succeeded" or not harvest.fetched:
        return PipelineResult(harvest=harvest)

    task = db.get(HarvestTask, task_id)
    source = db.get(DataSource, task.data_source_id)

    ingest = ingest_harvest_items(
        db,
        source,
        harvest.fetched,
        payload_of,
        blob_store,
        bucket_name=bucket_name,
        category_id=category_id,
        trace_id=trace_id,
        user_id=user_id,
    )
    detections = [
        run_detection(db, ds_id, blob_store, trace_id=trace_id, user_id=user_id)
        for ds_id in ingest.dataset_ids
    ]
    return PipelineResult(harvest=harvest, ingest=ingest, detection=detections)
