"""分层存储路由与迁移测试（W5 切片 A：热/温/冷迁移策略生效）。"""
from __future__ import annotations

import pytest

from app.services.storage import BlobStoreError, LocalBlobStore
from app.services.tiering import (
    StorageTierError,
    TieredBlobStores,
    validate_transition,
)


def test_validate_transition_directions():
    validate_transition("热", "温")
    validate_transition("热", "冷")
    validate_transition("温", "冷")
    with pytest.raises(StorageTierError):
        validate_transition("冷", "热")  # 向上禁止
    with pytest.raises(StorageTierError):
        validate_transition("温", "温")  # 平级禁止
    with pytest.raises(StorageTierError):
        validate_transition("热", "_archive")  # 未知层级


def test_tiered_stores_requires_all_tiers(tmp_path):
    with pytest.raises(StorageTierError):
        TieredBlobStores({"热": LocalBlobStore(tmp_path / "hot")})
    with pytest.raises(StorageTierError):
        TieredBlobStores({}).store_for("热")


def test_migrate_blob_moves_object_across_tiers(tier_stores):
    key, payload = "usgs/1/dem.tif", b"geo-bytes"
    tier_stores.store_for("热").put(key, payload)

    blob = tier_stores.migrate_blob(key, "热", "温")

    assert tier_stores.store_for("温").get(key) == payload
    assert not tier_stores.store_for("热").exists(key)  # 源端已删
    assert blob.checksum == __import__("hashlib").sha256(payload).hexdigest()


def test_migrate_blob_downward_only(tier_stores, tmp_path):
    tier_stores.store_for("冷").put("k", b"x")
    with pytest.raises(StorageTierError):
        tier_stores.migrate_blob("k", "冷", "热")


def test_migrate_blob_idempotent_replay(tier_stores):
    key, payload = "usgs/2/dem.tif", b"same"
    tier_stores.store_for("热").put(key, payload)
    tier_stores.store_for("温").put(key, payload)  # 目标已有同内容副本（上次迁移中断）
    tier_stores.migrate_blob(key, "热", "温")
    assert not tier_stores.store_for("热").exists(key)
    assert tier_stores.store_for("温").get(key) == payload


def test_migrate_blob_conflict_keeps_source(tier_stores):
    key = "usgs/3/dem.tif"
    tier_stores.store_for("热").put(key, b"new")
    tier_stores.store_for("温").put(key, b"different")  # 同键不同内容
    with pytest.raises(BlobStoreError):
        tier_stores.migrate_blob(key, "热", "温")
    assert tier_stores.store_for("热").get(key) == b"new"  # 源端保住


def test_migrate_blob_missing_source_raises(tier_stores):
    with pytest.raises(BlobStoreError):
        tier_stores.migrate_blob("nope", "热", "温")


def test_local_store_delete(tmp_path):
    store = LocalBlobStore(tmp_path)
    store.put("a/b.bin", b"x")
    store.delete("a/b.bin")
    assert not store.exists("a/b.bin")
    with pytest.raises(BlobStoreError):
        store.delete("a/b.bin")  # 再删报缺失
