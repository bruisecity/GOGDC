"""路由层共享依赖：会话 / 主体 / 存储与编目客户端（可注入）。"""

from typing import Optional

from fastapi import Depends

from ..config import settings
from ..core.auth import PrincipalDep, SessionDep  # noqa: F401
from ..services.catalog import PgstacCatalogClient
from ..services.storage import BlobStore, LocalBlobStore


def get_blob_store() -> BlobStore:
    """存储抽象装配：开发/file 路线用本地实现；生产注入 S3 兼容实现。"""
    return LocalBlobStore(settings.storage_dir)


def get_catalog_client() -> Optional[PgstacCatalogClient]:
    """pgSTAC 编目客户端：PGSTAC_DSN 未配置返回 None（STAC 路由回空响应）。"""
    if not settings.pgstac_dsn:
        return None
    from ..services.catalog import PsycopgExecutor

    return PgstacCatalogClient(PsycopgExecutor(settings.pgstac_dsn))


BlobStoreDep = Depends(get_blob_store)
CatalogClientDep = Depends(get_catalog_client)
