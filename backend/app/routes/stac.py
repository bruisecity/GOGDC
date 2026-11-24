"""STAC API 路由（设计稿 §7.1）：W3 起接入 pgSTAC 真实查询。

客户端经依赖注入（deps.get_catalog_client）：PGSTAC_DSN 未配置或
注入为 None 时返回合法空响应（W2 行为保持——目录检索降级可用）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from ..core.errors import NotFoundError
from ..schema import StacSearchIn
from ..services.catalog import PgstacCatalogClient
from .deps import CatalogClientDep

router = APIRouter(prefix="/stac/v1", tags=["stac"])

CONFORMANCE = [
    "https://api.stacspec.org/v1.1.0/core",
    "https://api.stacspec.org/v1.1.0/collections",
    "https://api.stacspec.org/v1.1.0/item-search",
    "https://api.stacspec.org/v1.1.0-rc1/ogcapi-features",
]


@router.get("")
@router.get("/")
def get_landing():
    """STAC landing page： conformsTo 声明 1.1.0 能力（匿名可读）。"""
    return {
        "stac_version": "1.1.0",
        "type": "Catalog",
        "id": "gogdc",
        "title": "Geodata 全球开放地理数据平台",
        "description": "GOGDC STAC 目录",
        "conformsTo": CONFORMANCE,
        "links": [
            {"rel": "self", "type": "application/json", "href": "/stac/v1"},
            {
                "rel": "data",
                "type": "application/json",
                "href": "/stac/v1/collections",
                "title": "数据集列表",
            },
            {
                "rel": "service-desc",
                "type": "application/openapi+json",
                "href": "/openapi.json",
            },
        ],
    }


@router.get("/collections")
def list_collections(client: Optional[PgstacCatalogClient] = CatalogClientDep):
    """集合列表：pgstac.all_collections()；未启用编目时返回空列表。"""
    if client is None:
        return {"collections": [], "links": []}
    collections = client.list_collections()
    return {"collections": collections, "links": []}


@router.get("/collections/{collection_id}")
def get_collection(collection_id: str, client: Optional[PgstacCatalogClient] = CatalogClientDep):
    """单个集合：pgstac.get_collection；不存在/未启用 → 404 信封。"""
    if client is None:
        raise NotFoundError(f"collection {collection_id} 不存在（编目未启用）")
    collection = client.get_collection(collection_id)
    if collection is None:
        raise NotFoundError(f"collection {collection_id} 不存在")
    return collection


@router.get("/collections/{collection_id}/items")
def list_items(
    collection_id: str,
    client: Optional[PgstacCatalogClient] = CatalogClientDep,
    limit: int = Query(default=10, ge=1, le=100),
):
    """集合条目：pgstac.search({collections:[id]})；未启用编目返回空集合。"""
    if client is None:
        return {"type": "FeatureCollection", "features": [], "links": [], "timeStamp": None}
    return client.search({"collections": [collection_id], "limit": limit})


@router.post("/search")
def search_items(
    payload: StacSearchIn,
    client: Optional[PgstacCatalogClient] = CatalogClientDep,
):
    """条目检索（STAC item-search 子集）：collections/ids/bbox/datetime/limit 透传 pgSTAC。"""
    if client is None:
        return {"type": "FeatureCollection", "features": [], "links": [], "timeStamp": None}
    body = payload.to_search_body()
    return client.search(body)
