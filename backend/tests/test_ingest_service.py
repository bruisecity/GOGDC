"""采集落盘服务测试（W3）。"""
from app.models import AuditLog, Dataset, StorageBucket, StorageObject
from app.services.ingest_service import ingest_harvest_items, storage_key_for
from app.services.usgs import HarvestItem


def _item(item_id="1001", fmt="IMG", url="https://example.gov/dl/ned_13.img"):
    return HarvestItem(id=item_id, title="NED tile", url=url, size_bytes=1024, format=fmt)


def _source(db_session):
    return db_session.query(__import__("app.models", fromlist=["DataSource"]).DataSource).one()


def test_ingest_creates_dataset_object_and_audit(db_session, blob_store, category):
    source = _source(db_session)
    summary = ingest_harvest_items(
        db_session,
        source,
        [_item()],
        lambda item: b"fake-tile-bytes",
        blob_store,
        bucket_name="geodata-hot",
        category_id=category.id,
        trace_id="trace-0001",
    )
    assert summary.ingested == 1 and summary.skipped == 0

    ds = db_session.query(Dataset).one()
    assert ds.security_status == "待检测"
    assert ds.storage_key == "usgs/1001/ned_13.img"
    assert ds.size_bytes == len(b"fake-tile-bytes")
    assert ds.collection_id is None

    obj = db_session.query(StorageObject).one()
    assert obj.bucket_id == db_session.query(StorageBucket).one().id
    assert obj.size_bytes == ds.size_bytes
    assert obj.checksum and len(obj.checksum) == 64

    bucket = db_session.query(StorageBucket).one()
    assert bucket.tier == "热"
    assert bucket.used_bytes == ds.size_bytes

    audit = db_session.query(AuditLog).filter(AuditLog.operation == "采集落盘").one()
    assert audit.detail["dataset_id"] == ds.id
    assert audit.detail["source_item_id"] == "1001"
    assert audit.trace_id == "trace-0001"
    assert blob_store.get(ds.storage_key) == b"fake-tile-bytes"


def test_ingest_is_idempotent_on_same_key(db_session, blob_store, category):
    source = _source(db_session)
    items = [_item()]
    first = ingest_harvest_items(
        db_session, source, items, lambda i: b"bytes", blob_store,
        bucket_name="geodata-hot", category_id=category.id,
    )
    second = ingest_harvest_items(
        db_session, source, items, lambda i: b"bytes", blob_store,
        bucket_name="geodata-hot", category_id=category.id,
    )
    assert first.ingested == 1
    assert second.ingested == 0 and second.skipped == 1
    assert db_session.query(Dataset).count() == 1
    assert db_session.query(StorageObject).count() == 1


def test_storage_key_format_fallback(db_session):
    item = HarvestItem(id="42", title="t", url="https://example.gov/query?id=42", size_bytes=1, format="CSV")
    assert storage_key_for("usgs", item) == "usgs/42/42.csv"
    item2 = HarvestItem(id="43", title="t", url="https://example.gov/query?id=43", size_bytes=1, format=None)
    assert storage_key_for("usgs", item2) == "usgs/43/43.bin"


def test_bucket_get_or_create_reuses(db_session):
    from app.services.ingest_service import get_or_create_bucket

    b1 = get_or_create_bucket(db_session, "geodata-hot")
    b2 = get_or_create_bucket(db_session, "geodata-hot")
    assert b1.id == b2.id
