"""编目批次服务（W3 切片 4：CatalogBatch 五步状态机 + pgSTAC 发布）。

状态机：草稿 → 字段校验 → 预览 → 发布 → 归档（domain/states.publish_transition，
只进不退、不可跳步）。进入「发布」时执行 pgSTAC 写入：
- upsert_collection（collection 幂等）；
- upsert_items（本轮 scope 内「检测通过」的数据集）；
- 回填 dataset.collection_id 与 batch.item_count。

批次 scope 口径：{"collection_id": "..."}，选取
security_status=检测通过 且（collection_id 为空或等于本批次 collection）的数据集。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..domain.states import BatchValidationError, publish_transition
from ..models import AuditLog, CatalogBatch, Dataset
from .catalog import PgstacCatalogClient, build_stac_collection, build_stac_item


def create_batch(
    db: Session,
    collection_id: str,
    *,
    trace_id: str | None = None,
    user_id: int | None = None,
) -> CatalogBatch:
    """创建草稿批次（batch_no 唯一：时间戳 + 随机后缀）。"""
    now = datetime.now(timezone.utc)
    batch = CatalogBatch(
        batch_no=f"B{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}",
        scope={"collection_id": collection_id},
        publish_state="草稿",
        version=1,
    )
    db.add(batch)
    db.flush()
    db.add(
        AuditLog(
            user_id=user_id,
            operation="编目批次创建",
            resource="catalog",
            result="success",
            detail={"batch_id": batch.id, "batch_no": batch.batch_no, "collection_id": collection_id},
            trace_id=trace_id,
        )
    )
    db.commit()
    return batch


def select_batch_datasets(db: Session, batch: CatalogBatch) -> list[Dataset]:
    """批次 scope 内可发布数据集：检测通过且未编目/已在目标 collection。"""
    collection_id = (batch.scope or {}).get("collection_id")
    return (
        db.query(Dataset)
        .filter(
            Dataset.security_status == "检测通过",
            Dataset.collection_id.is_(None) | (Dataset.collection_id == collection_id),
        )
        .order_by(Dataset.id)
        .all()
    )


def _validate_fields(datasets: list[Dataset]) -> list[str]:
    problems: list[str] = []
    for ds in datasets:
        if not (ds.storage_key or "").strip():
            problems.append(f"dataset={ds.id} 缺少 storage_key")
        if not (ds.format or "").strip():
            problems.append(f"dataset={ds.id} 缺少 format")
        bbox_vals = (ds.bbox_west, ds.bbox_south, ds.bbox_east, ds.bbox_north)
        if any(v is None for v in bbox_vals):
            problems.append(f"dataset={ds.id} 缺少空间范围（bbox）")
        elif not (
            float(ds.bbox_west) <= float(ds.bbox_east)
            and float(ds.bbox_south) <= float(ds.bbox_north)
        ):
            problems.append(f"dataset={ds.id} bbox 非法（west>east 或 south>north）")
    return problems


def advance_batch(
    db: Session,
    batch: CatalogBatch,
    target: str,
    client: PgstacCatalogClient | None = None,
    *,
    base_url: str = "",
    trace_id: str | None = None,
    user_id: int | None = None,
) -> CatalogBatch:
    """推进批次状态；进入「发布」时执行 pgSTAC 写入（幂等 upsert）。"""
    # 先校验流转合法性（不合法抛 InvalidTransition → 422）
    publish_transition(batch.publish_state, target)
    previous_state = batch.publish_state

    datasets = select_batch_datasets(db, batch)

    if target == "字段校验":
        problems = _validate_fields(datasets)
        if problems:
            raise BatchValidationError(f"字段校验未通过: {'; '.join(problems)}")

    item_count: int | None = None
    if target == "预览":
        item_count = len(datasets)  # 预览口径：本批将编目的条数

    if target == "发布":
        if client is None:
            raise BatchValidationError("pgSTAC 编目未启用，无法发布（PGSTAC_DSN 未配置）")
        problems = _validate_fields(datasets)
        if problems:
            raise BatchValidationError(f"发布前字段校验未通过: {'; '.join(problems)}")
        collection_id = (batch.scope or {}).get("collection_id")
        client.upsert_collection(build_stac_collection(collection_id, f"GOGDC {collection_id} 编目批次 {batch.batch_no}"))
        items = [
            build_stac_item(
                ds,
                collection_id=collection_id,
                download_url=f"{base_url}/api/v1/datasets/{ds.id}/download",
            )
            for ds in datasets
        ]
        client.upsert_items(items)
        for ds in datasets:
            ds.collection_id = collection_id
            db.add(ds)
        item_count = len(items)

    batch.publish_state = target
    if item_count is not None:
        batch.item_count = item_count
    db.add(batch)
    db.add(
        AuditLog(
            user_id=user_id,
            operation="STAC编目",
            resource="catalog",
            result="success",
            detail={
                "batch_id": batch.id,
                "batch_no": batch.batch_no,
                "from": previous_state,
                "target": target,
                "item_count": item_count,
            },
            trace_id=trace_id,
        )
    )
    db.commit()
    return batch
