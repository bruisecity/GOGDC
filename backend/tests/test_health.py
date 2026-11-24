"""API 健康接口测试（W1 验收：环境可部署 Hello 接口）。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


def test_hello():
    r = client.get("/hello")
    assert r.status_code == 200
    assert r.json()["message"] == "Hello, Geodata (GOGDC)!"


def test_readyz_shape():
    r = client.get("/readyz")
    assert r.status_code == 200
    assert set(r.json()["checks"]) == {"database", "redis", "storage"}
