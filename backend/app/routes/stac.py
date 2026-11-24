"""STAC API 空响应切片（设计稿 §7.1）。

W2 交付 /stac/v1 与 /collections、/collections/{id}/items 的
合法空响应；W3 起 pgSTAC 编目接入后替换为真实查询。
"""

from fastapi import APIRouter

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
        "description": "GOGDC STAC 目录（W2 空目录阶段）",
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
def list_collections():
    """空集合列表：STAC 1.1.0 合法的 0 条目录。"""
    return {"collections": [], "links": []}


@router.get("/collections/{collection_id}/items")
def list_items(collection_id: str):
    """空 items 响应：未知 collection 同样返回空（W3 接入 pgSTAC 后区分 404）。"""
    return {"type": "FeatureCollection", "features": [], "links": [], "timeStamp": None}
