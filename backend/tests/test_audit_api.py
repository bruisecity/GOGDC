"""审计查询 API 测试（W4：审计可查 + RBAC）。"""
from app.models import AuditLog
from tests.conftest import make_user


def _seed(db_session, n=5, trace_id="trace-a"):
    for i in range(n):
        db_session.add(AuditLog(
            user_id=None, operation="采集落盘" if i % 2 == 0 else "数据下载",
            resource="dataset", result="成功",
            detail={"i": i}, trace_id=trace_id if i < 3 else f"trace-{i}",
        ))
    db_session.commit()


def test_audit_requires_login(client, db_session):
    r = client.get("/api/console/audit-logs")
    assert r.status_code == 403
    assert r.json()["code"] == "PERMISSION_DENIED"


def test_audit_rbac_matrix(client, db_session):
    _seed(db_session, n=2)
    auditor = make_user(db_session, "a@example.com", "auditor")
    operator = make_user(db_session, "o@example.com", "operator")
    guest = make_user(db_session, "g@example.com", "guest")
    assert client.get("/api/console/audit-logs", headers={"X-API-Key": auditor}).status_code == 200
    assert client.get("/api/console/audit-logs", headers={"X-API-Key": operator}).status_code == 200
    r = client.get("/api/console/audit-logs", headers={"X-API-Key": guest})
    assert r.status_code == 403


def test_audit_filters_and_pagination(client, db_session):
    _seed(db_session, n=5)
    user = make_user(db_session, "q@example.com", "auditor")

    r = client.get("/api/console/audit-logs", headers={"X-API-Key": user})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    assert body["items"][0]["id"] > body["items"][-1]["id"]  # id desc
    assert set(body["items"][0]) >= {"id", "operation", "detail", "trace_id", "created_at"}

    r = client.get("/api/console/audit-logs?operation=数据下载",
                   headers={"X-API-Key": user})
    assert r.json()["total"] == 2
    assert all(x["operation"] == "数据下载" for x in r.json()["items"])

    r = client.get("/api/console/audit-logs?trace_id=trace-a",
                   headers={"X-API-Key": user})
    assert r.json()["total"] == 3

    r = client.get("/api/console/audit-logs?limit=2&offset=2",
                   headers={"X-API-Key": user})
    assert len(r.json()["items"]) == 2
    assert r.json()["total"] == 5
