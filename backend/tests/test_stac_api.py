"""STAC API 空响应切片测试（W2 验收：空响应可查）。"""


def test_landing_page():
    r = client_get("/stac/v1")
    assert r.status_code == 200
    body = r.json()
    assert body["stac_version"] == "1.1.0"
    assert any(c.endswith("/v1.1.0/core") for c in body["conformsTo"])
    rels = {link["rel"] for link in body["links"]}
    assert "data" in rels and "self" in rels


def test_collections_empty():
    r = client_get("/stac/v1/collections")
    assert r.status_code == 200
    assert r.json() == {"collections": [], "links": []}


def test_items_empty_feature_collection():
    r = client_get("/stac/v1/collections/any-id/items")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert body["features"] == []


def client_get(path):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        return c.get(path)


# ---- W3：注入 pgSTAC 客户端后的真实查询路径 ----

class FakeExecutor:
    """回放式假执行器：collections/items 由用例预置。"""

    def __init__(self, collections=None, search_result=None):
        self.collections = collections or []
        self.search_result = search_result
        self.searches: list[dict] = []

    def fetch_all(self, sql, params=()):
        return [None]

    def fetch_one(self, sql, params=()):
        import json

        if "all_collections()" in sql:
            return self.collections
        if "get_collection(" in sql:
            for c in self.collections:
                if c["id"] == params[0]:
                    return c
            return None
        if "search(" in sql:
            body = json.loads(params[0])
            self.searches.append(body)
            return self.search_result or {"type": "FeatureCollection", "features": []}
        return None


def client_with(**kwargs):
    """确定性作用域：TestClient 与依赖覆盖都在 with 块内建立/清理。

    不再用挂起的生成器（`next(gen)` 后残留 with 上下文，靠 GC 时机收尾，
    在部分环境下清理时机会与后续用例的依赖覆盖产生竞态）。
    """
    from contextlib import contextmanager

    from fastapi.testclient import TestClient

    from app.main import app
    from app.routes.deps import get_catalog_client
    from app.services.catalog import PgstacCatalogClient

    @contextmanager
    def _ctx():
        executor = FakeExecutor(**kwargs)
        app.dependency_overrides[get_catalog_client] = lambda: PgstacCatalogClient(executor)
        try:
            with TestClient(app) as c:
                yield c, executor
        finally:
            app.dependency_overrides.pop(get_catalog_client, None)

    return _ctx()


def test_collections_with_client():
    with client_with(collections=[{"id": "usgs-ned", "type": "Collection"}]) as (c, _):
        r = c.get("/stac/v1/collections")
        assert r.status_code == 200
        assert [x["id"] for x in r.json()["collections"]] == ["usgs-ned"]


def test_collection_detail_and_404():
    with client_with(collections=[{"id": "usgs-ned", "type": "Collection"}]) as (c, _):
        assert c.get("/stac/v1/collections/usgs-ned").status_code == 200
        r = c.get("/stac/v1/collections/nope")
        assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


def test_items_passthrough_search():
    result = {"type": "FeatureCollection", "features": [{"id": "ds-1"}], "numberReturned": 1}
    with client_with(search_result=result) as (c, ex):
        r = c.get("/stac/v1/collections/usgs-ned/items?limit=5")
        assert r.status_code == 200
        # 先断言执行器收到检索请求：可区分「路由未到达执行器」与「响应被替换为空」
        assert ex.searches[-1] == {"collections": ["usgs-ned"], "limit": 5}
        body = r.json()
        assert body["features"] == [{"id": "ds-1"}]


def test_post_search_forwards_body():
    result = {"type": "FeatureCollection", "features": [], "numberReturned": 0}
    with client_with(search_result=result) as (c, ex):
        r = c.post("/stac/v1/search", json={"collections": ["usgs-ned"], "ids": ["ds-1"], "limit": 3})
        assert r.status_code == 200
        assert ex.searches[-1] == {"collections": ["usgs-ned"], "ids": ["ds-1"], "limit": 3}


def test_search_invalid_bbox_422():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.post("/stac/v1/search", json={"bbox": [1.0, 2.0]})
        assert r.status_code == 422
        assert r.json()["code"] == "VALIDATION_ERROR"
