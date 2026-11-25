"""S3 兼容存储真机联调测试（W5：部署环境联调 MinIO / Ceph RGW）。

默认跳过；部署环境设置以下变量后运行（pytest tests/test_s3_integration.py）：
    S3_IT_ENDPOINT   如 http://127.0.0.1:19000（MinIO）或 http://rgw.ceph:8080
    S3_IT_ACCESS_KEY  / S3_IT_SECRET_KEY
    S3_IT_REGION     默认 us-east-1
每轮测试用独立桶名（it-<uuid> 后缀），不污染既有数据；结束后清理测试对象。
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.services.s3_store import S3CompatBlobStore
from app.services.storage import BlobStoreError
from app.services.tiering import TieredBlobStores

ENDPOINT = os.environ.get("S3_IT_ENDPOINT", "")
ACCESS_KEY = os.environ.get("S3_IT_ACCESS_KEY", "minioadmin")
SECRET_KEY = os.environ.get("S3_IT_SECRET_KEY", "minioadmin")
REGION = os.environ.get("S3_IT_REGION", "us-east-1")

pytestmark = pytest.mark.skipif(
    not ENDPOINT, reason="未设置 S3_IT_ENDPOINT，跳过真机联调（离线单测见 test_s3_store.py）"
)


def _store(bucket: str) -> S3CompatBlobStore:
    return S3CompatBlobStore(ENDPOINT, bucket, ACCESS_KEY, SECRET_KEY, region=REGION)


@pytest.fixture()
def hot():
    s = _store(f"it-hot-{uuid.uuid4().hex[:8]}")
    s.create_bucket()
    return s


@pytest.fixture()
def warm_cold():
    w = _store(f"it-warm-{uuid.uuid4().hex[:8]}")
    c = _store(f"it-cold-{uuid.uuid4().hex[:8]}")
    w.create_bucket()
    c.create_bucket()
    return w, c


def test_bucket_lifecycle_idempotent(hot):
    assert hot.bucket_exists() is True
    hot.create_bucket()  # 已存在：幂等不报错
    assert hot.bucket_exists() is True


def test_put_get_exists_delete_roundtrip(hot):
    key = "usgs/1/dem.tif"
    blob = hot.put(key, b"geo-payload-123")
    assert blob.size_bytes == 15
    assert hot.exists(key) is True
    assert hot.get(key) == b"geo-payload-123"

    hot.delete(key)
    assert hot.exists(key) is False
    with pytest.raises(BlobStoreError):
        hot.get(key)


def test_object_key_safety_on_real_cluster(hot):
    with pytest.raises(BlobStoreError):
        hot.put("/abs/key", b"x")
    with pytest.raises(BlobStoreError):
        hot.put("../escape", b"x")


def test_unicode_key_roundtrip(hot):
    key = "\u6570\u636e\u6e90/\u6761\u76ee1/\u6587\u4ef6 1.tif"
    hot.put(key, b"cjk")
    assert hot.exists(key) is True
    assert hot.get(key) == b"cjk"
    hot.delete(key)
    assert hot.exists(key) is False


def test_tier_migration_across_three_buckets(hot, warm_cold):
    """端到端联调验收：热->温->冷 全链路在真实 S3 兼容集群上迁移策略生效。"""
    warm, cold = warm_cold
    stores = TieredBlobStores({"\u70ed": hot, "\u6e29": warm, "\u51b7": cold})

    key, payload = "usgs/42/dem.tif", b"migration-payload" * 64
    stores.store_for("\u70ed").put(key, payload)

    stores.migrate_blob(key, "\u70ed", "\u6e29")
    assert warm.get(key) == payload
    assert not hot.exists(key)

    stores.migrate_blob(key, "\u6e29", "\u51b7")
    assert cold.get(key) == payload
    assert not warm.exists(key)
    assert not hot.exists(key)

    # 幂等重放路径：目标已有同内容副本时补删源不报错
    warm.put(key, payload)
    hot.put(key, payload)
    stores.migrate_blob(key, "\u70ed", "\u6e29")
    assert not hot.exists(key)
    assert warm.get(key) == payload

    if hot.exists(key):
        hot.delete(key)
    warm.delete(key)
    cold.delete(key)


def test_lifecycle_pass_end_to_end(db_session, hot, warm_cold):
    """真机端到端：生命周期引擎全流程（策略评估->blob 迁移->桶改挂->配额结转->审计）。"""
    import datetime as dt

    from app.models import AuditLog, StorageBucket, StorageObject
    from app.services.lifecycle_service import run_lifecycle_pass

    warm, _cold = warm_cold
    stores = TieredBlobStores({"\u70ed": hot, "\u6e29": warm, "\u51b7": _cold})

    row = StorageBucket(
        name="it-hot-row", tier="\u70ed",
        lifecycle_policy={"transitions": [
            {"target_tier": "\u6e29", "bucket": warm._bucket, "after_days": 0},
        ]},
        quota_bytes=1000, used_bytes=500,
    )
    db_session.add(row)
    db_session.flush()
    key = "usgs/77/dem.tif"
    db_session.add(StorageObject(
        bucket_id=row.id, key=key, size_bytes=500,
        last_access_at=dt.datetime(2026, 10, 1),
    ))
    db_session.commit()
    hot.put(key, b"lifecycle-e2e" * 20)

    result = run_lifecycle_pass(db_session, stores, user_id=None)

    assert result.migrated == 1
    assert result.transitions == {"\u70ed->\u6e29": 1}
    dst = db_session.query(StorageBucket).filter_by(name=warm._bucket).one()
    db_session.expire_all()
    assert dst.used_bytes == 500 and row.used_bytes == 0
    assert warm.get(key) == b"lifecycle-e2e" * 20
    assert not hot.exists(key)
    audit = db_session.query(AuditLog).filter(
        AuditLog.operation == "\u5b58\u50a8\u8fc1\u79fb").one()
    assert audit.detail["to_tier"] == "\u6e29"
    assert audit.detail["key"] == key

    # 清理
    warm.delete(key)
