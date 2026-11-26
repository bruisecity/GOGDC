"""数据总览聚合（W7 切片：总览面板真实 API）。

口径（设计稿 §4 监控三口径 + W6 四类失败）：
- 节点在线率：heartbeat_at 距 now 在阈值内（默认 300s，可经
  NODE_ONLINE_AFTER_SECONDS 配置）视为在线；
- 容量水位：沿用 W5 watermark 口径（used/quota，80% 告警线）；
- 采集失败率：按四类失败分别统计（HarvestTask.last_failure_class 口径，
  四键恒存在，与审计计数同口径）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Dataset,
    DatasetCategory,
    DataSource,
    HarvestTask,
    Node,
)
from .watermark_service import ALERT_RATIO, all_bucket_statuses

# 四类失败口径与 dispatch_service / failure-taxonomy 保持一致
FAILURE_CLASSES: tuple[str, ...] = ("连接超时", "源站限流", "凭证失效", "元数据校验")
NODE_ONLINE_AFTER_SECONDS = 300


def node_online(heartbeat_at: datetime | None, now: datetime) -> bool:
    """心跳在阈值内视为在线；从未上报心跳视为离线。"""
    if heartbeat_at is None:
        return False
    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return heartbeat_at >= now - timedelta(seconds=NODE_ONLINE_AFTER_SECONDS)


def build_overview(db: Session, *, now: datetime | None = None) -> dict:
    """聚合总览面板全部指标（纯读，不落库）。"""
    now = now or datetime.now(timezone.utc)

    dataset_total = db.query(Dataset).count()
    total_bytes = (
        db.query(func.coalesce(func.sum(Dataset.size_bytes), 0)).scalar() or 0
    )
    by_status: dict[str, int] = dict.fromkeys(
        ("待检测", "检测中", "检测通过", "已隔离"), 0
    )
    for status_, cnt in (
        db.query(Dataset.security_status, func.count(Dataset.id))
        .group_by(Dataset.security_status)
        .all()
    ):
        if status_ in by_status:
            by_status[status_] = cnt

    categories = db.query(DatasetCategory).count()

    nodes = db.query(Node).order_by(Node.id).all()
    online = sum(1 for n in nodes if node_online(n.heartbeat_at, now))
    online_rate = round(online / len(nodes), 4) if nodes else 0.0

    by_health: dict[str, int] = dict.fromkeys(("healthy", "degraded", "down"), 0)
    source_items = []
    for src in db.query(DataSource).order_by(DataSource.id).all():
        if src.health_status in by_health:
            by_health[src.health_status] += 1
        source_items.append(
            {
                "code": src.code,
                "health_status": src.health_status,
                "quota_limit": src.quota_limit or 0,
                "quota_used": src.quota_used or 0,
                "last_success_at": src.last_success_at,
            }
        )

    task_total = db.query(HarvestTask).count()
    by_task_status: dict[str, int] = dict.fromkeys(
        ("idle", "running", "failed", "paused"), 0
    )
    for status_, cnt in (
        db.query(HarvestTask.status, func.count(HarvestTask.id))
        .group_by(HarvestTask.status)
        .all()
    ):
        if status_ in by_task_status:
            by_task_status[status_] = cnt

    failures: dict[str, int] = dict.fromkeys(FAILURE_CLASSES, 0)
    for cls, cnt in (
        db.query(HarvestTask.last_failure_class, func.count(HarvestTask.id))
        .filter(HarvestTask.last_failure_class.isnot(None))
        .group_by(HarvestTask.last_failure_class)
        .all()
    ):
        if cls in failures:
            failures[cls] = cnt

    bucket_rows = all_bucket_statuses(db)
    total_used = sum(b["used_bytes"] for b in bucket_rows)
    total_quota = sum(b["quota_bytes"] for b in bucket_rows)
    # watermark 口径键为 bucket/tier；总览对外统一为 name/tier
    bucket_rows = [
        {**{k: v for k, v in b.items() if k != "bucket"},
         "name": b["bucket"], "tier": b["tier"]}
        for b in bucket_rows
    ]

    return {
        "datasets": {
            "total": dataset_total,
            "total_bytes": int(total_bytes),
            "by_security_status": by_status,
        },
        "dataset_categories": categories,
        "nodes": {
            "total": len(nodes),
            "online": online,
            "online_rate": online_rate,
        },
        "data_sources": {"by_health": by_health, "items": source_items},
        "harvest_tasks": {"total": task_total, "by_status": by_task_status},
        "harvest_failures": failures,
        "storage": {
            "alert_ratio": ALERT_RATIO,
            "total_used_bytes": total_used,
            "total_quota_bytes": total_quota,
            "buckets": bucket_rows,
        },
        "generated_at": now,
    }
