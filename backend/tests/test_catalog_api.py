"""编目批次控制台 API 测试（W3：RBAC + 状态机 + 发布信封）。"""
import pytest

from app.main import app
from app.models import Dataset
from app.routes.deps import get_catalog_client
from app.services.catalog import PgstacCatalogClient
from tests.conftest import make_user


class FakeExecutor:
    def fetch_all(self, sql, params=()):
        return [None]

    def fetch_one(self, sql, params=()):
        if "all_collections()" in sql:
            return []
        return None


@pytest.fixture()
def api(client, db_session, monkeypatch):
    """TestClient + 编目客户端注入（PGSTAC_DSN 未配置场景下覆盖依赖）。"""
    app.dependency_overrides[get_catalog_client] = lambda: PgstacCatalogClient(FakeExecutor())
    yield client
    app.dependency_overrides.pop(get_catalog_client, None)


def _mk_dataset(db, category, *, status="检测通过"):
    from app.models import Dataset

    ds = Dataset(
        category_id=category.id, format="IMG", size_bytes=10,
        security_status=status, storage_key="usgs/1/a.img",
    )
    db.add(ds)
    db.commit()
    return ds


def test_rbac_matrix_on_catalog(api, db_session):
    reader = make_user(db_session, "aud@example.com", "auditor")
    writer = make_user(db_session, "op@example.com", "operator")
    guest = make_user(db_session, "g@example.com", "guest")

    assert api.get("/api/console/catalog-batches").status_code == 403  # 匿名
    r = api.get("/api/console/catalog-batches", headers={"X-API-Key": guest})
    assert r.status_code == 403
    r = api.get("/api/console/catalog-batches", headers={"X-API-Key": reader})
    assert r.status_code == 200 and r.json()["total"] == 0
    r = api.post("/api/console/catalog-batches", headers={"X-API-Key": reader},
                 json={"collection_id": "usgs-ned"})
    assert r.status_code == 403  # auditor 只有读

    r = api.post("/api/console/catalog-batches", headers={"X-API-Key": writer},
                 json={"collection_id": "usgs-ned"})
    assert r.status_code == 201
    body = r.json()
    assert body["publish_state"] == "草稿"
    assert body["scope"] == {"collection_id": "usgs-ned"}


def test_advance_to_publish_full_walk(api, db_session, category):
    writer = make_user(db_session, "op@example.com", "operator")
    _mk_dataset(db_session, category)
    batch = api.post("/api/console/catalog-batches", headers={"X-API-Key": writer},
                     json={"collection_id": "usgs-ned"}).json()

    r = api.patch(f"/api/console/catalog-batches/{batch['id']}",
                  headers={"X-API-Key": writer}, json={"target": "字段校验"})
    assert r.status_code == 200 and r.json()["publish_state"] == "字段校验"

    r = api.patch(f"/api/console/catalog-batches/{batch['id']}",
                  headers={"X-API-Key": writer}, json={"target": "预览"})
    assert r.status_code == 200 and r.json()["item_count"] == 1

    r = api.patch(f"/api/console/catalog-batches/{batch['id']}",
                  headers={"X-API-Key": writer}, json={"target": "发布"})
    assert r.status_code == 200 and r.json()["publish_state"] == "发布"
    ds = db_session.query(Dataset).one()
    assert ds.collection_id == "usgs-ned"


def test_skip_step_rejected_422(api, db_session, category):
    writer = make_user(db_session, "op@example.com", "operator")
    batch = api.post("/api/console/catalog-batches", headers={"X-API-Key": writer},
                     json={"collection_id": "usgs-ned"}).json()
    r = api.patch(f"/api/console/catalog-batches/{batch['id']}",
                  headers={"X-API-Key": writer}, json={"target": "发布"})
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_STATE_TRANSITION"


def test_get_batch_404(api, db_session):
    writer = make_user(db_session, "op@example.com", "operator")
    r = api.get("/api/console/catalog-batches/999", headers={"X-API-Key": writer})
    assert r.status_code == 404
