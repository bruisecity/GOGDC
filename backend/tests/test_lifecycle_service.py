"""生命周期规则引擎测试（W5 切片 B：规则评估 + 迁移执行 + 审计）。"""
from __future__ import annotations

import datetime as dt

import pytest

from app.models import AuditLog, StorageBucket, StorageObject
from app.services.lifecycle_service import (
    LifecyclePolicyError,
    parse_policy,
    plan_transitions,
    run_lifecycle_pass,
)

NOW = dt.datetime(2026, 12, 25, 12, 0, 0)
POLICY = {
    "transitions": [
        {"target_tier": "温", "bucket": "geodata-warm", "after_days": 30},
        {"target_tier": "冷", "bucket": "geodata-cold", "after_days": 180},
    ]
}


def _bucket(db, name="geodata-hot", tier="热", policy=POLICY, quota=0):
    b = StorageBucket(name=name, tier=tier, lifecycle_policy=policy,
                      quota_bytes=quota, used_bytes=0)
    db.add(b)
    db.flush()
    return b


def _object(db, bucket_id, key, days_idle, size=100):
    obj = StorageObject(
        bucket_id=bucket_id, key=key, size_bytes=size,
        last_access_at=NOW - dt.timedelta(days=days_idle),
    )
    db.add(obj)
    db.flush()
    return obj


# ---- 策略解析 ----

def test_parse_policy_empty_means_none():
    """空策略合法（未配置生命周期），与非法配置区分开。"""
    assert parse_policy(None) == []
    assert parse_policy({}) == []


@pytest.mark.parametrize("policy", [
    {"transitions": []},
    {"transitions": "x"},
    {"transitions": [{"target_tier": "档", "bucket": "b", "after_days": 1}]},
    {"transitions": [{"target_tier": "温", "bucket": "", "after_days": 1}]},
    {"transitions": [{"target_tier": "温", "bucket": "b", "after_days": -1}]},
    {"transitions": [{"target_tier": "温", "bucket": "b", "after_days": "30"}]},
])
def test_parse_policy_rejects_invalid(policy):
    with pytest.raises(LifecyclePolicyError):
        parse_policy(policy)


def test_parse_policy_accepts_valid():
    specs = parse_policy(POLICY)
    assert [(s.target_tier, s.bucket, s.after_days) for s in specs] == [
        ("温", "geodata-warm", 30), ("冷", "geodata-cold", 180)
    ]


# ---- 计划 ----

def test_plan_transitions_age_and_deepest(db_session):
    bucket = _bucket(db_session)
    old = _object(db_session, bucket.id, "old", days_idle=200)
    mid = _object(db_session, bucket.id, "mid", days_idle=45)
    fresh = _object(db_session, bucket.id, "fresh", days_idle=3)
    plans = dict(plan_transitions(bucket, [old, mid, fresh], NOW))
    assert plans[old].target_tier == "冷"   # 200 天 → 最深一档，直接落冷
    assert plans[mid].target_tier == "温"
    assert fresh not in plans


def test_plan_transitions_upward_policy_rejected(db_session):
    bucket = _bucket(db_session, tier="冷", policy={
        "transitions": [{"target_tier": "热", "bucket": "x", "after_days": 1}]
    })
    with pytest.raises(LifecyclePolicyError):
        plan_transitions(bucket, [], NOW)


def test_plan_transitions_last_access_fallback_created(db_session):
    bucket = _bucket(db_session, policy={
        "transitions": [{"target_tier": "温", "bucket": "w", "after_days": 30}]
    })
    obj = StorageObject(bucket_id=bucket.id, key="no-access",
                        size_bytes=1)  # last_access_at 为空 → 回退 created_at
    db_session.add(obj)
    db_session.flush()
    obj.created_at = NOW - dt.timedelta(days=60)
    plans = plan_transitions(bucket, [obj], NOW)
    assert obj in dict(plans)


# ---- 执行 ----

def test_run_lifecycle_pass_migrates_objects_and_audits(db_session, tier_stores):
    hot = _bucket(db_session, quota=10_000)
    stale = _object(db_session, hot.id, "usgs/1/a.tif", days_idle=45, size=400)
    fresh = _object(db_session, hot.id, "usgs/1/b.tif", days_idle=2, size=600)
    hot.used_bytes = 1000
    tier_stores.store_for("热").put("usgs/1/a.tif", b"x" * 400)
    tier_stores.store_for("热").put("usgs/1/b.tif", b"y" * 600)

    result = run_lifecycle_pass(db_session, tier_stores, now=NOW)

    assert result.migrated == 1
    assert result.transitions == {"热->温": 1}
    # 桶归属与物理 blob 同步迁移
    warm = db_session.query(StorageBucket).filter_by(name="geodata-warm").one()
    assert stale.bucket_id == warm.id and warm.tier == "温"
    assert not tier_stores.store_for("热").exists("usgs/1/a.tif")
    assert tier_stores.store_for("温").get("usgs/1/a.tif") == b"x" * 400
    assert tier_stores.store_for("热").exists("usgs/1/b.tif")  # 未到期不动
    # 配额结转
    db_session.refresh(hot); db_session.refresh(warm)
    assert hot.used_bytes == 600 and warm.used_bytes == 400
    # 审计：一条存储迁移成功
    rows = db_session.query(AuditLog).filter(AuditLog.operation == "存储迁移").all()
    assert len(rows) == 1
    d = rows[0].detail
    assert d["from_tier"] == "热" and d["to_tier"] == "温"
    assert d["key"] == "usgs/1/a.tif" and d["size_bytes"] == 400


def test_run_lifecycle_pass_idempotent(db_session, tier_stores):
    hot = _bucket(db_session)
    obj = _object(db_session, hot.id, "usgs/1/a.tif", days_idle=45)
    tier_stores.store_for("热").put("usgs/1/a.tif", b"z")

    first = run_lifecycle_pass(db_session, tier_stores, now=NOW)
    second = run_lifecycle_pass(db_session, tier_stores, now=NOW)

    assert first.migrated == 1
    assert second.migrated == 0  # 已在温桶，无策略则不再迁移
    migrated = db_session.query(AuditLog).filter(
        AuditLog.operation == "存储迁移").count()
    assert migrated == 1


def test_run_lifecycle_pass_invalid_policy_recorded_not_raised(db_session, tier_stores):
    hot = _bucket(db_session, policy={"transitions": "bad"})
    _object(db_session, hot.id, "k", days_idle=365)

    result = run_lifecycle_pass(db_session, tier_stores, now=NOW)

    assert result.migrated == 0
    assert result.policy_failures == [hot.name]
    failed = db_session.query(AuditLog).filter(
        AuditLog.operation == "生命周期", AuditLog.result == "失败").one()
    assert failed.detail["bucket"] == hot.name


def test_run_lifecycle_pass_chained_next_pass(db_session, tier_stores):
    """温桶也配策略：第二轮把冷闲置对象继续下沉到冷档。"""
    hot = _bucket(db_session)
    obj = _object(db_session, hot.id, "usgs/9/c.tif", days_idle=200)
    tier_stores.store_for("热").put("usgs/9/c.tif", b"c")

    run_lifecycle_pass(db_session, tier_stores, now=NOW)  # 热→冷（最深档）
    run_lifecycle_pass(db_session, tier_stores, now=NOW)  # 幂等复跑

    cold = db_session.query(StorageBucket).filter_by(name="geodata-cold").one()
    assert obj.bucket_id == cold.id
    assert tier_stores.store_for("冷").get("usgs/9/c.tif") == b"c"
    assert not tier_stores.store_for("热").exists("usgs/9/c.tif")


def test_run_lifecycle_pass_target_tier_mismatch_policy(db_session, tier_stores):
    """目标桶已存在且层级与策略不一致：记失败不迁移。"""
    hot = _bucket(db_session)
    _object(db_session, hot.id, "k", days_idle=60)
    tier_stores.store_for("热").put("k", b"k")
    db_session.add(StorageBucket(name="geodata-warm", tier="冷",
                                 quota_bytes=0, used_bytes=0))
    db_session.flush()

    result = run_lifecycle_pass(db_session, tier_stores, now=NOW)

    assert result.migrated == 0
    assert result.policy_failures == [hot.name]
    assert tier_stores.store_for("热").exists("k")  # 数据未动
