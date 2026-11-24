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
