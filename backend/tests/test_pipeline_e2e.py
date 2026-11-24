"""端到端验收测试（W3 验收口径）：一条 USGS 数据从采集到可检索、可下载。

链路：FakeTransport（USGS 目录）→ run_collect_pipeline（采集→落盘→检测）
→ 编目批次（草稿→字段校验→预览→发布，pgSTAC 假执行器）
→ /stac/v1/collections/{id}/items 可检索 → /api/v1/datasets/{id}/download 可下载。
全程审计日志逐段落库。
"""
import json

from app.main import app
from app.models import AuditLog, Dataset, HarvestTask
from app.routes.deps import get_blob_store, get_catalog_client
from app.services.catalog import PgstacCatalogClient
from app.services.catalog_service import advance_batch, create_batch
from app.services.pipeline import run_collect_pipeline
from app.services.usgs import USGSTNMClient
from fastapi.testclient import TestClient


PRODUCTS_PAYLOAD = {
    "products": [
        {
            "id": 900001,
            "title": "NED 1/3 arc-second tile",
            "downloadURL": "https://example.gov/dl/ned_900001.img",
            "sizeInBytes": 2048,
            "format": "IMG",
        }
    ]
}


class FakeTransport:
    def request(self, method, url, params):
        return 200, PRODUCTS_PAYLOAD


class RecordingExecutor:
    """记录写入并支持回放 search 的假 pgSTAC 执行器。"""

    def __init__(self):
        self.collections: list[dict] = []
        self.items: list[dict] = []

    def fetch_all(self, sql, params=()):
        if "upsert_collection" in sql:
            self.collections.append(json.loads(params[0]))
        elif "upsert_items" in sql:
            self.items.extend(json.loads(params[0]))
        return [None]

    def fetch_one(self, sql, params=()):
        if "all_collections()" in sql:
            return self.collections
        if "get_collection(" in sql:
            for c in self.collections:
                if c["id"] == params[0]:
                    return c
            return None
        if "search(" in sql:
            body = json.loads(params[0])
            cids = body.get("collections", [])
            feats = [i for i in self.items if not cids or i["collection"] in cids]
            return {"type": "FeatureCollection", "features": feats, "numberReturned": len(feats)}
        return None


def test_end_to_end_usgs_to_download(db_session, blob_store, category, detection_rules):
    # 1) 建采集任务（conftest 已种 usgs 数据源 + node-1 节点）
    task = HarvestTask(data_source_id=1, node_id=1, retry_max=3, status="idle")
    db_session.add(task)
    db_session.commit()

    # 2) 采集→落盘→检测 全流水线（出网不可达，payload 用合成字节）
    pipeline = run_collect_pipeline(
        db_session,
        task.id,
        USGSTNMClient(FakeTransport()),
        blob_store,
        lambda item: b"fake-ned-tile-" + item.id.encode(),
        category_id=category.id,
        bucket_name="geodata-hot",
        trace_id="e2e-trace",
    )
    assert pipeline.harvest.outcome == "succeeded"
    assert pipeline.harvest.items == 1
    assert pipeline.ingest.ingested == 1
    assert pipeline.detection[0].status == "检测通过"

    dataset = db_session.query(Dataset).one()
    assert dataset.security_status == "检测通过"
    assert dataset.storage_key == "usgs/900001/ned_900001.img"

    # 3) 编目批次发布（pgSTAC 写入经记录式执行器）
    executor = RecordingExecutor()
    client = PgstacCatalogClient(executor)
    batch = create_batch(db_session, "usgs-ned", trace_id="e2e-trace")
    for target in ("字段校验", "预览", "发布"):
        advance_batch(db_session, batch, target, client, base_url="", trace_id="e2e-trace")
    assert batch.publish_state == "发布" and batch.item_count == 1
    assert executor.collections and executor.collections[0]["id"] == "usgs-ned"
    assert len(executor.items) == 1
    assert executor.items[0]["collection"] == "usgs-ned"
    assert db_session.get(Dataset, dataset.id).collection_id == "usgs-ned"

    # 4) 目录检索（STAC 路由走注入客户端）
    from app.db import get_db

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_catalog_client] = lambda: client
    app.dependency_overrides[get_blob_store] = lambda: blob_store
    try:
        with TestClient(app) as http:
            r = http.get("/stac/v1/collections")
            assert r.status_code == 200
            assert [c["id"] for c in r.json()["collections"]] == ["usgs-ned"]

            r = http.get("/stac/v1/collections/usgs-ned/items")
            assert r.status_code == 200
            features = r.json()["features"]
            assert len(features) == 1
            assert features[0]["id"] == f"ds-{dataset.id}"
            assert features[0]["assets"]["data"]["href"].endswith(
                f"/api/v1/datasets/{dataset.id}/download"
            )

            # 5) 匿名下载（公开数据）
            r = http.get(features[0]["assets"]["data"]["href"])
            assert r.status_code == 200
            assert r.content == b"fake-ned-tile-900001"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_catalog_client, None)
        app.dependency_overrides.pop(get_blob_store, None)

    # 6) 审计链完整：采集落盘 / 安全检测 / STAC编目 / 数据下载
    operations = {
        a.operation for a in db_session.query(AuditLog).filter(AuditLog.trace_id == "e2e-trace")
    }
    assert {"采集落盘", "安全检测", "STAC编目"} <= operations
    assert "数据下载" in {a.operation for a in db_session.query(AuditLog)}
