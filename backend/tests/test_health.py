"""API 健康接口测试（W2：readyz 为真实探针，测试用 monkeypatch 替换）。"""
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


def test_readyz_all_probes_ok(monkeypatch):
    monkeypatch.setattr("app.core.probes.probe_database", lambda engine: (True, "select1=ok pgstac=yes"))
    monkeypatch.setattr("app.core.probes.probe_redis", lambda url: (True, "ping=pong"))
    monkeypatch.setattr("app.core.probes.probe_storage", lambda r, e: (True, "route=object status=403"))
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"database", "redis", "storage"}
    assert all(c["status"] == "ok" for c in body["checks"].values())


def test_readyz_degraded_returns_503(monkeypatch):
    monkeypatch.setattr("app.core.probes.probe_database", lambda engine: (True, "select1=ok pgstac=yes"))
    monkeypatch.setattr("app.core.probes.probe_redis", lambda url: (False, "ping-failed: ConnectionError"))
    monkeypatch.setattr("app.core.probes.probe_storage", lambda r, e: (True, "route=object status=403"))
    r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"]["status"] == "fail"
