"""pgSTAC 真库集成测试（CI migrate-smoke 作业执行，本地跳过）。

运行条件：环境变量 GEODATA_PGSTAC_DSN 指向已装 pgSTAC 的 Postgres。
契约口径来自 pgstac 0.9.8 官方迁移 SQL（pypgstac 包内置）：
upsert_collection / upsert_items / all_collections / get_collection / search。
此测试就是 W3 catalog 客户端 SQL 的最终裁决（假执行器单测只验调用形态）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("GEODATA_PGSTAC_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="需 GEODATA_PGSTAC_DSN 指向真实 pgSTAC 库")

from app.services.catalog import PgstacCatalogClient, PsycopgExecutor  # noqa: E402


def _collection(cid: str) -> dict:
    return {
        "type": "Collection",
        "stac_version": "1.1.0",
        "id": cid,
        "description": "geodata W3 集成测试集合",
        "license": "various",
        "extent": {
            "spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]},
            "temporal": {"interval": [[None, None]]},
        },
        "links": [],
    }


def _item(cid: str, item_id: str, dataset_id: int = 1) -> dict:
    return {
        "type": "Feature",
        "stac_version": "1.1.0",
        "id": item_id,
        "collection": cid,
        "geometry": None,
        "bbox": None,
        "properties": {
            "datetime": datetime.now(timezone.utc).isoformat(),
            "geodata:dataset_id": dataset_id,
        },
        "assets": {
            "data": {"href": f"/api/v1/datasets/{dataset_id}/download", "type": "application/octet-stream"}
        },
        "links": [],
    }


@pytest.fixture()
def client() -> PgstacCatalogClient:
    return PgstacCatalogClient(PsycopgExecutor(DSN))


def test_roundtrip_collection_items_search(client: PgstacCatalogClient):
    cid = f"itest-{uuid.uuid4().hex[:8]}"
    items = [_item(cid, "ds-1", 1), _item(cid, "ds-2", 2)]
    try:
        # 幂等写入
        client.upsert_collection(_collection(cid))
        client.upsert_items(items)
        client.upsert_collection(_collection(cid))  # 重复 upsert 不报错
        client.upsert_items(items)

        got = client.get_collection(cid)
        assert got is not None and got["id"] == cid

        assert cid in {c["id"] for c in client.list_collections()}

        result = client.search({"collections": [cid], "limit": 10})
        assert result["type"] == "FeatureCollection"
        assert result["numberReturned"] == 2
        ids = {f["id"] for f in result["features"]}
        assert ids == {"ds-1", "ds-2"}

        # 属性透传：自定义属性随 jsonb 原样往返
        for f in result["features"]:
            assert f["properties"]["geodata:dataset_id"] in (1, 2)

        # 按 id 精确检索
        single = client.search({"collections": [cid], "ids": ["ds-1"], "limit": 10})
        assert single["numberReturned"] == 1
    finally:
        for it in items:
            try:
                PgstacCatalogClient(PsycopgExecutor(DSN))._executor.fetch_all(
                    "SELECT pgstac.delete_item(%s, %s)", (it["id"], cid)
                )
            except Exception:
                pass
        try:
            PgstacCatalogClient(PsycopgExecutor(DSN))._executor.fetch_all(
                "SELECT pgstac.delete_collection(%s)", (cid,)
            )
        except Exception:
            pass
