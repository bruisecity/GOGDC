"""存储分层路由与热/温/冷迁移（W5 存储分级切片）。

口径（技术方案第四章）：
- 三档层级：热（在线高频读写）/ 温（低频访问）/ 冷（归档），只允许向下迁移；
- 迁移 = 目标端写入 + 内容回读校验 + 源端删除；校验不过绝不删源（宁可冗余不可丢数据）；
- storage_key 全局相对键不变，迁移只改对象所属桶（tier 挂在桶上）。
"""
from __future__ import annotations

import hashlib

from .s3_store import S3CompatBlobStore
from .storage import BlobStore, BlobStoreError, LocalBlobStore, StoredBlob

TIER_ORDER: dict[str, int] = {"热": 0, "温": 1, "冷": 2}
TIERS: tuple[str, ...] = ("热", "温", "冷")


class StorageTierError(Exception):
    """层级非法或迁移方向非法。"""


def validate_transition(src_tier: str, dst_tier: str) -> None:
    """只允许 热→温 / 热→冷 / 温→冷（向下迁移）。"""
    if src_tier not in TIER_ORDER or dst_tier not in TIER_ORDER:
        raise StorageTierError(f"未知层级: {src_tier!r} -> {dst_tier!r}")
    if TIER_ORDER[dst_tier] <= TIER_ORDER[src_tier]:
        raise StorageTierError(f"层级只能向下迁移: {src_tier} -> {dst_tier}")


class TieredBlobStores:
    """三档 BlobStore 路由：按桶的 tier 找到对应物理存储。"""

    def __init__(self, stores: dict[str, BlobStore]) -> None:
        missing = [t for t in TIERS if t not in stores]
        if missing:
            raise StorageTierError(f"分层存储缺少层级实现: {missing}")
        self._stores = dict(stores)

    def store_for(self, tier: str) -> BlobStore:
        if tier not in self._stores:
            raise StorageTierError(f"未知层级: {tier!r}")
        return self._stores[tier]

    def migrate_blob(self, key: str, from_tier: str, to_tier: str) -> StoredBlob:
        """跨层级搬移一个对象：写目标 → 回读校验 → 删源。"""
        validate_transition(from_tier, to_tier)
        src, dst = self.store_for(from_tier), self.store_for(to_tier)

        payload = src.get(key)  # 源缺失即报错，不静默
        if dst.exists(key):
            dst_blob = dst.get(key)
            if hashlib.sha256(dst_blob).hexdigest() == hashlib.sha256(payload).hexdigest():
                src.delete(key)  # 幂等重放：目标已有同内容副本，补删源即可
                return StoredBlob(len(dst_blob), hashlib.sha256(payload).hexdigest())
            raise BlobStoreError(f"目标端已存在同键不同内容对象: {key!r}")

        stored = dst.put(key, payload)
        # 回读校验：内容逐字节一致才允许删源
        if dst.get(key) != payload:
            raise BlobStoreError(f"迁移后回读校验失败（内容不一致）: {key!r}")
        src.delete(key)
        return stored


def default_tier_stores() -> TieredBlobStores:
    """按配置装配三档存储（worker / 控制台触发用）。

    file 路线：三档各一个本地目录（热档沿用 W3 的 STORAGE_DIR，向后兼容）；
    object 路线：S3 兼容三桶（endpoint 同一集群）。
    """
    from ..config import settings

    if settings.storage_route == "file":
        return TieredBlobStores(
            {
                "热": LocalBlobStore(settings.storage_dir),
                "温": LocalBlobStore(settings.storage_warm_dir),
                "冷": LocalBlobStore(settings.storage_cold_dir),
            }
        )
    if not settings.s3_access_key or not settings.s3_secret_key:
        raise BlobStoreError("object 路线需配置 S3_ACCESS_KEY / S3_SECRET_KEY")
    return TieredBlobStores(
        {
            "热": S3CompatBlobStore(
                settings.s3_endpoint, settings.s3_bucket,
                settings.s3_access_key, settings.s3_secret_key, region=settings.s3_region,
            ),
            "温": S3CompatBlobStore(
                settings.s3_endpoint, settings.s3_warm_bucket,
                settings.s3_access_key, settings.s3_secret_key, region=settings.s3_region,
            ),
            "冷": S3CompatBlobStore(
                settings.s3_endpoint, settings.s3_cold_bucket,
                settings.s3_access_key, settings.s3_secret_key, region=settings.s3_region,
            ),
        }
    )
