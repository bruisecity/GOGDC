"""存储分层与水位控制台 API（W5：热/温/冷迁移 + 容量告警可达）。

- GET  /api/console/storage/buckets              桶清单 + 水位快照（storage:读）
- POST /api/console/storage/buckets/{id}/policy   配置生命周期策略（storage:写）
- POST /api/console/storage/lifecycle/run         触发一轮迁移（storage:写）
- GET  /api/console/storage/watermark             水位快照（storage:读）
- POST /api/console/storage/watermark/check       巡检并落告警/恢复审计（storage:写）
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..core.auth import PrincipalDep, require
from ..models import AuditLog, StorageBucket
from ..schema import (
    LifecyclePolicyIn,
    LifecycleRunOut,
    StorageBucketListOut,
    StorageBucketOut,
    WatermarkCheckOut,
    WatermarkBucketOut,
    WatermarkStatusOut,
)
from ..services.lifecycle_service import LifecyclePolicyError, run_lifecycle_pass
from ..services.watermark_service import (
    ALERT_RATIO,
    all_bucket_statuses,
    bucket_status,
    check_watermarks,
)
from ..services.tiering import TieredBlobStores
from .deps import SessionDep, TierStoresDep

router = APIRouter(prefix="/api/console/storage", tags=["storage"])


def _bucket_out(bucket: StorageBucket) -> StorageBucketOut:
    status = bucket_status(bucket)
    return StorageBucketOut(
        id=bucket.id,
        name=bucket.name,
        tier=bucket.tier,
        lifecycle_policy=bucket.lifecycle_policy or {},
        quota_bytes=bucket.quota_bytes or 0,
        used_bytes=bucket.used_bytes or 0,
        versioning_enabled=bool(bucket.versioning_enabled),
        object_lock_days=bucket.object_lock_days,
        watermark_ratio=status["ratio"],
        watermark_status=status["status"],
    )


@router.get("/buckets")
def list_buckets(principal: PrincipalDep, db: SessionDep):
    require("storage", "读")(principal)
    rows = db.query(StorageBucket).order_by(StorageBucket.id).all()
    return StorageBucketListOut(
        items=[_bucket_out(r) for r in rows], total=len(rows)
    )


@router.post("/buckets/{bucket_id}/policy")
def set_policy(
    bucket_id: int, body: LifecyclePolicyIn, principal: PrincipalDep, db: SessionDep
):
    require("storage", "写")(principal)
    bucket = (
        db.query(StorageBucket).filter(StorageBucket.id == bucket_id).one_or_none()
    )
    if bucket is None:
        raise HTTPException(status_code=404, detail="桶不存在")
    policy = body.model_dump()
    # 立即校验：目标档必须是本桶的向下迁移，写库前暴露配置错误
    from ..services.lifecycle_service import parse_policy
    from ..services.tiering import TIER_ORDER

    for spec in parse_policy(policy):
        if TIER_ORDER[spec.target_tier] <= TIER_ORDER[bucket.tier]:
            raise HTTPException(
                status_code=422,
                detail=f"目标档 {spec.target_tier} 必须低于本桶层级 {bucket.tier}",
            )
    bucket.lifecycle_policy = policy
    db.add(AuditLog(
        user_id=principal.user.id, operation="策略配置", resource="storage",
        result="成功", detail={"bucket": bucket.name, "policy": policy},
    ))
    db.commit()
    db.refresh(bucket)
    return _bucket_out(bucket)


@router.post("/lifecycle/run")
def lifecycle_run(principal: PrincipalDep, db: SessionDep,
                  stores: TieredBlobStores = TierStoresDep):
    require("storage", "写")(principal)
    result = run_lifecycle_pass(db, stores, user_id=principal.user.id)
    return LifecycleRunOut(
        migrated=result.migrated,
        transitions=result.transitions,
        policy_failures=result.policy_failures,
    )


@router.get("/watermark")
def watermark_status(principal: PrincipalDep, db: SessionDep):
    require("storage", "读")(principal)
    statuses = all_bucket_statuses(db)
    return WatermarkStatusOut(
        alert_ratio=ALERT_RATIO,
        buckets=[WatermarkBucketOut(**s) for s in statuses],
    )


@router.post("/watermark/check")
def watermark_check(principal: PrincipalDep, db: SessionDep):
    require("storage", "写")(principal)
    before = db.query(AuditLog).filter(AuditLog.operation == "容量水位").count()
    alerts = check_watermarks(db, user_id=principal.user.id)
    after = db.query(AuditLog).filter(AuditLog.operation == "容量水位").count()
    return WatermarkCheckOut(
        alerts=[WatermarkBucketOut(**a) for a in alerts],
        audited=after > before,
    )
