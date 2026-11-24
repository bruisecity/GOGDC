"""pgSTAC 客户端 + 编目批次服务测试（W3，假执行器离线跑）。"""
import json

from app.models import AuditLog, Dataset
from app.services.catalog import PgstacCatalogClient, build_stac_collection, build_stac_item
from app.services.catalog_service import (
    advance_batch,
    create_batch,
    select_batch_datasets,
)
from app.domain.states import BatchValidationError, InvalidTransition


class FakeExecutor:
    """记录调用并按需回放结果的假执行器。"""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.collections: dict[str, dict] = {}
        self.items: list[dict] = []

    def fetch_all(self, sql, params=()):
        self.calls.append((sql, params))
        return [None]

    def fetch_one(self, sql, params=()):
        self.calls.append((sql, params))
        if "all_collections()" in sql:
            return list(self.collections.values())
        if "get_collection(" in sql:
            return self.collections.get(params[0])
        if "search(" in sql:
            cid = json.loads(params[0]).get("collections", [None])[0]
            feats = [i for i in self.items if i["collection"] == cid]
            return {"type": "FeatureCollection", "features": feats, "numberReturned": len(feats)}
        return None


def _client():
    return FakeExecutor()


def _mk_dataset(db_session, category, *, status="检测通过", collection=None, ds_id=None, fmt="IMG"):
    ds = Dataset(
        category_id=category.id, format=fmt, size_bytes=100,
        security_status=status, storage_key=f"usgs/{ds_id or 1}/a.img", collection_id=collection,
    )
    db_session.add(ds)
    db_session.commit()
    return ds


# ---- 客户端 SQL 契约 ----

def test_client_sql_contract():
    ex = _client()
    client = PgstacCatalogClient(ex)
    client.upsert_collection({"id": "c1"})
    client.upsert_items([{"id": "i1", "collection": "c1"}])
    assert ex.calls[0] == ("SELECT pgstac.upsert_collection(%s::jsonb)", ('{"id": "c1"}',))
    assert ex.calls[1][0] == "SELECT pgstac.upsert_items(%s::jsonb)"
    client.list_collections()
    client.get_collection("c1")
    client.search({"collections": ["c1"], "limit": 10})
    sqls = " | ".join(sql for sql, _ in ex.calls)
    assert "pgstac.all_collections()" in sqls
    assert "pgstac.get_collection(%s)" in sqls
    assert "pgstac.search(%s::jsonb)" in sqls


def test_client_search_returns_empty_on_non_dict():
    class EmptyExecutor:
        def fetch_all(self, sql, params=()):
            return []

        def fetch_one(self, sql, params=()):
            return None

    client = PgstacCatalogClient(EmptyExecutor())
    assert client.search({"collections": ["x"]})["features"] == []
    assert client.list_collections() == []
    assert client.get_collection("x") is None


# ---- STAC JSON 构建 ----

def test_build_stac_item_shape(db_session, category):
    ds = _mk_dataset(db_session, category)
    item = build_stac_item(ds, collection_id="usgs-ned", download_url=f"http://portal/api/v1/datasets/{ds.id}/download")
    assert item["type"] == "Feature"
    assert item["stac_version"] == "1.1.0"
    assert item["id"] == f"ds-{ds.id}"
    assert item["collection"] == "usgs-ned"
    assert item["properties"]["geodata:dataset_id"] == ds.id
    assert item["assets"]["data"]["href"].endswith(f"/datasets/{ds.id}/download")
    assert item["assets"]["data"]["type"] == "image/tiff; application=geotiff"


def test_build_stac_collection_shape():
    c = build_stac_collection("usgs-ned", "desc")
    assert c["type"] == "Collection"
    assert c["license"] == "various"
    assert c["id"] == "usgs-ned"
    assert c["extent"]["spatial"]["bbox"]


# ---- 批次服务 ----

def test_create_batch_defaults(db_session):
    batch = create_batch(db_session, "usgs-ned", trace_id="t-9")
    assert batch.publish_state == "草稿"
    assert batch.batch_no.startswith("B")
    assert batch.scope == {"collection_id": "usgs-ned"}
    audit = db_session.query(AuditLog).filter(AuditLog.operation == "编目批次创建").one()
    assert audit.detail["collection_id"] == "usgs-ned"


def test_publish_walks_states_and_ingests(db_session, category):
    ds = _mk_dataset(db_session, category, ds_id=1)
    quarantined = _mk_dataset(db_session, category, ds_id=2, status="已隔离")
    batch = create_batch(db_session, "usgs-ned")

    advance_batch(db_session, batch, "字段校验", base_url="http://p")
    assert batch.publish_state == "字段校验"

    advance_batch(db_session, batch, "预览", base_url="http://p")
    assert batch.publish_state == "预览"
    assert batch.item_count == 1  # 只统计检测通过

    ex = _client()
    advance_batch(
        db_session, batch, "发布", PgstacCatalogClient(ex), base_url="http://p"
    )
    assert batch.publish_state == "发布"
    assert batch.item_count == 1
    assert db_session.get(Dataset, ds.id).collection_id == "usgs-ned"
    assert db_session.get(Dataset, quarantined.id).collection_id is None

    upsert_collection_called = any("upsert_collection" in sql for sql, _ in ex.calls)
    upsert_items_called = any("upsert_items" in sql for sql, _ in ex.calls)
    assert upsert_collection_called and upsert_items_called
    body = json.loads([p for sql, p in ex.calls if "upsert_items" in sql][0][0])
    assert len(body) == 1 and body[0]["collection"] == "usgs-ned"

    audit = db_session.query(AuditLog).filter(AuditLog.operation == "STAC编目").all()
    assert any(a.detail.get("target") == "发布" and a.detail.get("item_count") == 1 for a in audit)


def test_illegal_transition_rejected(db_session):
    batch = create_batch(db_session, "usgs-ned")
    try:
        advance_batch(db_session, batch, "发布")  # 草稿跳到发布
        assert False, "应拒绝跳步"
    except InvalidTransition:
        pass


def test_publish_without_client_rejected(db_session, category):
    _mk_dataset(db_session, category, ds_id=1)
    batch = create_batch(db_session, "usgs-ned")
    advance_batch(db_session, batch, "字段校验")
    advance_batch(db_session, batch, "预览")
    try:
        advance_batch(db_session, batch, "发布", None)
        assert False, "未配置 pgSTAC 应拒绝"
    except BatchValidationError as exc:
        assert "PGSTAC_DSN" in str(exc)


def test_field_validation_failure_keeps_state(db_session, category):
    ds = _mk_dataset(db_session, category, ds_id=1, fmt="")
    batch = create_batch(db_session, "usgs-ned")
    try:
        advance_batch(db_session, batch, "字段校验")
        assert False, "缺 format 应校验失败"
    except BatchValidationError:
        pass
    assert batch.publish_state == "草稿"


def test_select_scope_excludes_other_collection(db_session, category):
    _mk_dataset(db_session, category, ds_id=1)
    _mk_dataset(db_session, category, ds_id=2, collection="other-collection")
    batch = create_batch(db_session, "usgs-ned")
    ids = [d.id for d in select_batch_datasets(db_session, batch)]
    assert len(ids) == 1
