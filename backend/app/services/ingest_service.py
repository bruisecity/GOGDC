"""采集落盘服务（W3 切片 1：USGS 条目 → Dataset + StorageObject + 审计）。

数据流（设计稿 §6）：采集调度 → 汇聚节点落地（本模块）→ 安全检测
→ STAC 编目 → 存储分级 → 门户检索/下载，每步落审计日志。

口径：
- 存储键 `storage_key` = `{source_code}/{source_item_id}/{文件名}`，全局相对键；
- 同一 (bucket, key) 已存在则跳过（采集重放幂等）；
- Dataset.security_status 初始「待检测」，由检测服务推进；
- blob 内容由调用方通过 payload_of 回调提供（生产=worker 下载，测试=合成字节），
  服务本身不感知出网能力。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from ..models import AuditLog, DataSource, Dataset, StorageBucket, StorageObject
from .storage import BlobStore
from .usgs import HarvestItem

# 源侧 format → 存储扩展名（TNM 常见口径）
_FORMAT_EXT: dict[str, str] = {
    "IMG": "img",
    "GeoTIFF": "tif",
    "TIFF": "tif",
    "ArcGrid": "zip",
    "CSV": "csv",
    "FileGDB": "zip",
    "Shapefile": "zip",
}


@dataclass
class IngestSummary:
    """一次落盘的结果汇总。"""

    ingested: int = 0
    skipped: int = 0
    dataset_ids: list[int] = field(default_factory=list)


def _object_name(item: HarvestItem) -> str:
    """从下载 URL 提取文件名；无路径名时按 format 推扩展名兜底。"""
    path = urlparse(item.url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else ""
    if name and "." in name:
        return name
    ext = _FORMAT_EXT.get((item.format or "").strip(), "bin")
    return f"{item.id}.{ext}"


def storage_key_for(source_code: str, item: HarvestItem) -> str:
    """全局相对存储键。"""
    return f"{source_code}/{item.id}/{_object_name(item)}"


def get_or_create_bucket(db: Session, name: str) -> StorageBucket:
    """按名称取存储桶，缺失则建默认「热」桶。"""
    bucket = db.query(StorageBucket).filter(StorageBucket.name == name).one_or_none()
    if bucket is None:
        bucket = StorageBucket(name=name, tier="热", quota_bytes=0, used_bytes=0)
        db.add(bucket)
        db.flush()
    return bucket


def ingest_harvest_items(
    db: Session,
    source: DataSource,
    items: list[HarvestItem],
    payload_of: Callable[[HarvestItem], bytes],
    blob_store: BlobStore,
    *,
    bucket_name: str,
    category_id: int,
    trace_id: str | None = None,
    user_id: int | None = None,
) -> IngestSummary:
    """把一批采集条目落盘为 Dataset + StorageObject。

    幂等：已存在同 (bucket, key) 对象的条目跳过。调用方负责传入有效
    category_id；本函数统一 commit。
    """
    bucket = get_or_create_bucket(db, bucket_name)
    summary = IngestSummary()

    for item in items:
        key = storage_key_for(source.code, item)
        existing = (
            db.query(StorageObject)
            .filter(StorageObject.bucket_id == bucket.id, StorageObject.key == key)
            .one_or_none()
        )
        if existing is not None:
            summary.skipped += 1
            continue

        payload = payload_of(item)
        blob = blob_store.put(key, payload)

        dataset = Dataset(
            category_id=category_id,
            format=(item.format or "").strip(),
            size_bytes=blob.size_bytes,
            license=None,  # 源侧许可口径未确证前不落值（W4 补）
            security_status="待检测",
            storage_key=key,
        )
        db.add(dataset)
        db.flush()  # 取 dataset.id 供审计

        db.add(
            StorageObject(
                bucket_id=bucket.id,
                key=key,
                etag=None,
                checksum=blob.checksum,
                size_bytes=blob.size_bytes,
            )
        )
        bucket.used_bytes = (bucket.used_bytes or 0) + blob.size_bytes
        summary.ingested += 1
        summary.dataset_ids.append(dataset.id)
        db.add(
            AuditLog(
                user_id=user_id,
                operation="采集落盘",
                resource="dataset",
                result="success",
                detail={
                    "dataset_id": dataset.id,
                    "storage_key": key,
                    "source_item_id": item.id,
                    "source": source.code,
                    "size_bytes": blob.size_bytes,
                    "checksum": blob.checksum,
                },
                trace_id=trace_id,
            )
        )

    db.commit()
    return summary
