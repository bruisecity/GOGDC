"""编目批次控制台（W3：设计稿 §7.2 控制台侧 catalog 面板）。

- GET/POST /api/console/catalog-batches：批次列表与创建（草稿）；
- GET /{id}：批次详情；
- POST /{id}/advance：五步状态机推进（草稿→字段校验→预览→发布→归档），
  进入「发布」时执行 pgSTAC 写入（需 PGSTAC_DSN 已配置且角色有 catalog:写）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, status

from ..config import settings
from ..core.auth import PrincipalDep, require
from ..core.errors import NotFoundError
from ..models import CatalogBatch
from ..schema import (
    CatalogBatchAdvance,
    CatalogBatchCreate,
    CatalogBatchListOut,
    CatalogBatchOut,
)
from ..services.catalog import PgstacCatalogClient
from ..services.catalog_service import advance_batch, create_batch
from .deps import CatalogClientDep, SessionDep

router = APIRouter(prefix="/api/console/catalog-batches", tags=["catalog"])


def _out(batch: CatalogBatch) -> CatalogBatchOut:
    return CatalogBatchOut.model_validate(batch)


@router.get("")
def list_batches(
    principal: PrincipalDep,
    db: SessionDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    require("catalog", "读")(principal)
    total = db.query(CatalogBatch).count()
    rows = (
        db.query(CatalogBatch).order_by(CatalogBatch.id.desc()).offset(offset).limit(limit).all()
    )
    return CatalogBatchListOut(items=[_out(r) for r in rows], total=total)


@router.post("", status_code=status.HTTP_201_CREATED)
def post_batch(payload: CatalogBatchCreate, principal: PrincipalDep, db: SessionDep):
    require("catalog", "写")(principal)
    batch = create_batch(
        db,
        payload.collection_id,
        trace_id=None,
        user_id=None if principal.anonymous else principal.user.id,
    )
    return _out(batch)


@router.get("/{batch_id}")
def get_batch(batch_id: int, principal: PrincipalDep, db: SessionDep):
    require("catalog", "读")(principal)
    batch = db.get(CatalogBatch, batch_id)
    if batch is None:
        raise NotFoundError(f"编目批次 {batch_id} 不存在")
    return _out(batch)


@router.patch("/{batch_id}")
def advance(
    batch_id: int,
    payload: CatalogBatchAdvance,
    principal: PrincipalDep,
    db: SessionDep,
    client: Optional[PgstacCatalogClient] = CatalogClientDep,
):
    """推进批次状态（target ∈ 五步之一）；非法流转 422、字段校验不过 422。"""
    require("catalog", "写")(principal)
    batch = db.get(CatalogBatch, batch_id)
    if batch is None:
        raise NotFoundError(f"编目批次 {batch_id} 不存在")
    batch = advance_batch(
        db,
        batch,
        payload.target,
        client,
        base_url=settings.public_base_url,
        user_id=None if principal.anonymous else principal.user.id,
    )
    return _out(batch)
