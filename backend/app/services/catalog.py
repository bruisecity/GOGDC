"""pgSTAC 编目客户端（W3 切片 3）。

SQL 契约取自 pgstac 0.9.8 官方迁移源码（pypgstac 包内置，权威口径）：
- upsert_collection(data jsonb) / upsert_items(data jsonb)  —— 幂等写入
- get_collection(id text) / all_collections()               —— 读取
- search(jsonb) RETURNS jsonb                                —— FeatureCollection
  （features / numberReturned / numberMatched / next / prev）
所有函数自带 `SET SEARCH_PATH TO pgstac,public`，schema 限定调用即可。

执行器可注入（与 W2 Transport 同思路）：测试用假执行器离线跑，
生产/CI 用 PsycopgExecutor 连真实 pgSTAC。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from ..models import Dataset

STAC_VERSION = "1.1.0"

# format → asset MIME（常见地理格式，未命中回退 octet-stream）
FORMAT_MIME: dict[str, str] = {
    "img": "image/tiff; application=geotiff",
    "tif": "image/tiff; application=geotiff",
    "tiff": "image/tiff; application=geotiff",
    "csv": "text/csv",
    "zip": "application/zip",
    "json": "application/json",
}


class CatalogExecutor(Protocol):
    """SQL 执行器协议：单列结果取首列，无结果集返回空列表。"""

    def fetch_all(self, sql: str, params: tuple = ()) -> list[Any]: ...

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[Any]: ...


class PsycopgExecutor:
    """psycopg3 执行器：每次操作独立连接（W3 简单口径，池化留 W6）。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        import psycopg  # 延迟导入：本地/单测无 psycopg 也能导入本模块

        return psycopg.connect(self._dsn)

    def fetch_all(self, sql: str, params: tuple = ()) -> list[Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description is None:
                    return []
                return [row[0] for row in cur.fetchall()]

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[Any]:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None


class PgstacCatalogClient:
    """pgSTAC 编目读写门面（所有 SQL 集中在此模块，便于升级对齐）。"""

    def __init__(self, executor: CatalogExecutor) -> None:
        self._executor = executor

    # ---- 写入 ----

    def upsert_collection(self, collection: dict) -> None:
        self._executor.fetch_all(
            "SELECT pgstac.upsert_collection(%s::jsonb)", (json.dumps(collection),)
        )

    def upsert_items(self, items: list[dict]) -> None:
        if not items:
            return
        self._executor.fetch_all(
            "SELECT pgstac.upsert_items(%s::jsonb)", (json.dumps(items),)
        )

    # ---- 读取 ----

    def list_collections(self) -> list[dict]:
        row = self._executor.fetch_one("SELECT pgstac.all_collections()")
        return list(row) if isinstance(row, list) else []

    def get_collection(self, collection_id: str) -> Optional[dict]:
        return self._executor.fetch_one(
            "SELECT pgstac.get_collection(%s)", (collection_id,)
        )

    def search(self, body: dict) -> dict:
        row = self._executor.fetch_one(
            "SELECT pgstac.search(%s::jsonb)", (json.dumps(body),)
        )
        return row if isinstance(row, dict) else {"type": "FeatureCollection", "features": []}


# ---- STAC JSON 构建 ----


def asset_mime(storage_key: str) -> str:
    suffix = storage_key.rsplit(".", 1)[-1].lower() if "." in storage_key else ""
    return FORMAT_MIME.get(suffix, "application/octet-stream")


def build_stac_collection(collection_id: str, description: str) -> dict:
    """构造合法 STAC Collection（license=various：条目许可逐条落，不整体虚构）。"""
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": collection_id,
        "description": description,
        "license": "various",
        "extent": {"spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]}, "temporal": {"interval": [[None, None]]}},
        "links": [],
    }


def build_stac_item(dataset: Dataset, *, collection_id: str, download_url: str) -> dict:
    """Dataset → STAC Item（bbox/时间范围缺省为 null，pgstac 仅强制 properties.datetime）。"""
    bbox = None
    if (
        dataset.bbox_west is not None
        and dataset.bbox_south is not None
        and dataset.bbox_east is not None
        and dataset.bbox_north is not None
    ):
        bbox = [
            float(dataset.bbox_west),
            float(dataset.bbox_south),
            float(dataset.bbox_east),
            float(dataset.bbox_north),
        ]
    created = dataset.created_at or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "id": f"ds-{dataset.id}",
        "collection": collection_id,
        "geometry": None,
        "bbox": bbox,
        "properties": {
            "datetime": created.isoformat(),
            "geodata:dataset_id": dataset.id,
            "geodata:format": dataset.format or "",
            "geodata:size_bytes": dataset.size_bytes or 0,
        },
        "assets": {
            "data": {
                "href": download_url,
                "type": asset_mime(dataset.storage_key),
                "title": dataset.storage_key.rsplit("/", 1)[-1],
            }
        },
        "links": [],
    }
