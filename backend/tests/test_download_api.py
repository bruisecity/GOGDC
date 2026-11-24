"""下载路由测试（W3：匿名可下 / 隔离 403 / 未检测 409 / 额度扣减）。"""
import pytest

from app.models import AuditLog, Dataset, StorageObject
from app.routes.deps import get_blob_store
from tests.conftest import make_user


@pytest.fixture()
def api(client, blob_store, db_session):
    """在 TestClient 上覆盖 blob 存储依赖。"""
    from app.main import app

    app.dependency_overrides[get_blob_store] = lambda: blob_store
    yield client
    app.dependency_overrides.pop(get_blob_store, None)


def _mk_dataset(db, category, *, status="检测通过", key="usgs/1/a.img", size=None):
    ds = Dataset(
        category_id=category.id, format="IMG", size_bytes=size or 2048,
        security_status=status, storage_key=key,
    )
    db.add(ds)
    db.flush()
    return ds


def test_anonymous_download_ok(api, db_session, category, blob_store):
    ds = _mk_dataset(db_session, category)
    blob_store.put(ds.storage_key, b"tile-bytes")
    r = api.get(f"/api/v1/datasets/{ds.id}/download")
    assert r.status_code == 200
    assert r.content == b"tile-bytes"
    assert r.headers["content-type"].startswith("image/tiff")
    assert "attachment" in r.headers["content-disposition"]
    audit = db_session.query(AuditLog).filter(AuditLog.operation == "数据下载").one()
    assert audit.user_id is None
    assert audit.detail["dataset_id"] == ds.id


def test_quarantined_forbidden(api, db_session, category):
    ds = _mk_dataset(db_session, category, status="已隔离")
    r = api.get(f"/api/v1/datasets/{ds.id}/download")
    assert r.status_code == 403
    assert r.json()["code"] == "PERMISSION_DENIED"


def test_not_ready_conflict(api, db_session, category):
    ds = _mk_dataset(db_session, category, status="待检测")
    r = api.get(f"/api/v1/datasets/{ds.id}/download")
    assert r.status_code == 409
    assert r.json()["code"] == "CONFLICT"


def test_missing_dataset_404(api, db_session, category):
    r = api.get("/api/v1/datasets/999/download")
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


def test_quota_enforced_and_decremented(api, db_session, category, blob_store):
    ds = _mk_dataset(db_session, category, size=1000)
    blob_store.put(ds.storage_key, b"x" * 1000)
    key = make_user(db_session, "quota@example.com", "researcher", download_quota=500)
    r = api.get(f"/api/v1/datasets/{ds.id}/download", headers={"X-API-Key": key})
    assert r.status_code == 403
    assert "额度不足" in r.json()["message"]

    ds2 = _mk_dataset(db_session, category, key="usgs/2/b.img", size=300)
    blob_store.put(ds2.storage_key, b"y" * 300)
    r = api.get(f"/api/v1/datasets/{ds2.id}/download", headers={"X-API-Key": key})
    assert r.status_code == 200
    from app.models import AppUser

    user = db_session.query(AppUser).filter(AppUser.email == "quota@example.com").one()
    assert user.download_quota == 200  # 500 - 300
    audit = db_session.query(AuditLog).filter(AuditLog.operation == "数据下载").one()
    assert audit.user_id == user.id


def test_zero_quota_means_unlimited(api, db_session, category, blob_store):
    ds = _mk_dataset(db_session, category, size=10**9)
    blob_store.put(ds.storage_key, b"z")
    key = make_user(db_session, "free@example.com", "guest", download_quota=0)
    r = api.get(f"/api/v1/datasets/{ds.id}/download", headers={"X-API-Key": key})
    assert r.status_code == 200
