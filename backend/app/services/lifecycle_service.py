"""生命周期规则引擎与迁移执行（W5 切片 B）。

策略口径（StorageBucket.lifecycle_policy JSON）：
```
{"transitions": [
    {"target_tier": "温", "bucket": "geodata-warm", "after_days": 30},
    {"target_tier": "冷", "bucket": "geodata-cold", "after_days": 180}
]}
```
- 按对象「最近访问」（last_access_at，缺失回退 created_at）计算闲置天数；
- 同时满足多条时取最深的一档（闲置 200 天直接 热→冷，不中途落温）；
- 只允许向下迁移（热→温→冷），由 tiering.validate_transition 强制；
- 每次迁移落一条审计日志（operation=存储迁移），迁移后桶的 used_bytes 双向结转。
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..models import AuditLog, StorageBucket, StorageObject
from .tiering import StorageTierError, TieredBlobStores, TIER_ORDER, validate_transition


class LifecyclePolicyError(Exception):
    """生命周期策略配置非法。"""


@dataclass(frozen=True)
class TransitionSpec:
    target_tier: str
    bucket: str
    after_days: int


@dataclass
class LifecyclePassResult:
    migrated: int = 0
    transitions: dict[str, int] = field(default_factory=dict)  # "热->温" -> n
    policy_failures: list[str] = field(default_factory=list)   # 桶名
    skipped: int = 0


def parse_policy(policy: dict | None) -> list[TransitionSpec]:
    """解析并校验策略；空策略返回 []，非法策略抛 LifecyclePolicyError。"""
    if not policy:
        return []
    if not isinstance(policy, dict) or "transitions" not in policy:
        raise LifecyclePolicyError("策略必须是含 transitions 数组的对象")
    raw = policy["transitions"]
    if not isinstance(raw, list) or not raw:
        raise LifecyclePolicyError("transitions 必须为非空数组")
    specs: list[TransitionSpec] = []
    for item in raw:
        if not isinstance(item, dict):
            raise LifecyclePolicyError(f"transition 必须为对象: {item!r}")
        tier = item.get("target_tier")
        bucket = item.get("bucket")
        days = item.get("after_days")
        if tier not in TIER_ORDER:
            raise LifecyclePolicyError(f"target_tier 非法: {tier!r}")
        if not bucket or not isinstance(bucket, str):
            raise LifecyclePolicyError(f"bucket 非法: {bucket!r}")
        if not isinstance(days, int) or isinstance(days, bool) or days < 0:
            raise LifecyclePolicyError(f"after_days 必须为非负整数: {days!r}")
        specs.append(TransitionSpec(target_tier=tier, bucket=bucket, after_days=days))
    return specs


def _age_days(obj: StorageObject, now: dt.datetime) -> int:
    anchor = obj.last_access_at or obj.created_at
    if anchor is None:
        return 0
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return max(0, (now - anchor).days)


def plan_transitions(
    bucket: StorageBucket, objects: list[StorageObject], now: dt.datetime
) -> list[tuple[StorageObject, TransitionSpec]]:
    """对桶内对象逐一决策：返回 (对象, 目标档) 列表。只做计划不执行。"""
    specs = [s for s in parse_policy(bucket.lifecycle_policy)]
    # 策略在本桶上必须整体向下
    for s in specs:
        if TIER_ORDER[s.target_tier] <= TIER_ORDER[bucket.tier]:
            raise LifecyclePolicyError(
                f"桶 {bucket.name}({bucket.tier}) 的目标档 {s.target_tier} 非向下迁移"
            )
    plans: list[tuple[StorageObject, TransitionSpec]] = []
    for obj in objects:
        age = _age_days(obj, now)
        eligible = [s for s in specs if age >= s.after_days]
        if not eligible:
            continue
        deepest = max(eligible, key=lambda s: s.after_days)
        plans.append((obj, deepest))
    return plans


def _get_or_create_tier_bucket(db: Session, name: str, tier: str) -> StorageBucket:
    """按名称取桶；缺失则建指定层级桶，已存在则校验层级一致。"""
    bucket = db.query(StorageBucket).filter(StorageBucket.name == name).one_or_none()
    if bucket is None:
        bucket = StorageBucket(name=name, tier=tier, quota_bytes=0, used_bytes=0)
        db.add(bucket)
        db.flush()
    elif bucket.tier != tier:
        raise LifecyclePolicyError(
            f"目标桶 {name} 已存在且层级为 {bucket.tier}，与策略 {tier} 不一致"
        )
    return bucket


def run_lifecycle_pass(
    db: Session,
    stores: TieredBlobStores,
    *,
    now: dt.datetime | None = None,
    user_id: int | None = None,
) -> LifecyclePassResult:
    """执行一轮生命周期迁移：计划 → 搬移 blob → 改挂桶 + 结转配额 → 审计。"""
    now = now or dt.datetime.now(tz=dt.timezone.utc)
    trace_id = f"lifecycle:{uuid.uuid4().hex[:12]}"
    result = LifecyclePassResult()

    for bucket in db.query(StorageBucket).order_by(StorageBucket.id).all():
        try:
            specs = parse_policy(bucket.lifecycle_policy)
            objects = (
                db.query(StorageObject)
                .filter(StorageObject.bucket_id == bucket.id)
                .all()
            )
            plans = plan_transitions(bucket, objects, now)
        except (LifecyclePolicyError, StorageTierError) as exc:
            result.policy_failures.append(bucket.name)
            db.add(AuditLog(
                user_id=user_id, operation="生命周期", resource="storage",
                result="失败", detail={"bucket": bucket.name, "error": str(exc)},
                trace_id=trace_id,
            ))
            continue

        for obj, spec in plans:
            try:
                dst = _get_or_create_tier_bucket(db, spec.bucket, spec.target_tier)
            except LifecyclePolicyError as exc:
                # 目标桶已存在但层级不符：记失败审计，跳过该桶本轮迁移
                result.policy_failures.append(bucket.name)
                db.add(AuditLog(
                    user_id=user_id, operation="生命周期", resource="storage",
                    result="失败", detail={"bucket": bucket.name, "error": str(exc)},
                    trace_id=trace_id,
                ))
                break
            if dst.id == bucket.id:
                result.policy_failures.append(bucket.name)
                continue
            key, size = obj.key, obj.size_bytes
            # 幂等重放：目标桶已有同键对象 → 补删源端（blob+行），不再搬移
            replayed = (
                db.query(StorageObject)
                .filter(StorageObject.bucket_id == dst.id, StorageObject.key == key)
                .one_or_none()
            )
            if replayed is not None:
                stores.store_for(bucket.tier).delete(key)
                db.delete(obj)
                result.migrated += 1
                result.transitions[f"{bucket.tier}->{spec.target_tier}"] = (
                    result.transitions.get(f"{bucket.tier}->{spec.target_tier}", 0) + 1
                )
                continue

            stores.migrate_blob(key, bucket.tier, dst.tier)
            obj.bucket_id = dst.id
            bucket.used_bytes = max(0, (bucket.used_bytes or 0) - (size or 0))
            dst.used_bytes = (dst.used_bytes or 0) + (size or 0)
            db.add(AuditLog(
                user_id=user_id, operation="存储迁移", resource="storage", result="成功",
                detail={
                    "key": key, "from_bucket": bucket.name, "to_bucket": dst.name,
                    "from_tier": bucket.tier, "to_tier": dst.tier,
                    "size_bytes": size,
                },
                trace_id=trace_id,
            ))
            result.migrated += 1
            result.transitions[f"{bucket.tier}->{spec.target_tier}"] = (
                result.transitions.get(f"{bucket.tier}->{spec.target_tier}", 0) + 1
            )

    db.commit()
    return result
