"""检测规则控制台 API 测试（W4）。"""
from tests.conftest import make_user


def test_rules_rbac_and_list(client, db_session, detection_rules):
    reader = make_user(db_session, "op@example.com", "operator")
    guest = make_user(db_session, "g@example.com", "guest")
    assert client.get("/api/console/detection-rules").status_code == 403
    assert client.get("/api/console/detection-rules",
                      headers={"X-API-Key": guest}).status_code == 403
    r = client.get("/api/console/detection-rules", headers={"X-API-Key": reader})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    types = {x["rule_type"] for x in body["items"]}
    assert types == {"元数据完整性", "病毒载荷"}
    assert all(set(x) >= {"id", "rule_type", "params", "severity", "action", "enabled"} for x in body["items"])


def test_rule_toggle_requires_write(client, db_session, detection_rules):
    writer = make_user(db_session, "w@example.com", "operator")
    auditor = make_user(db_session, "a@example.com", "auditor")
    rule = detection_rules["virus"]
    r = client.patch(f"/api/console/detection-rules/{rule.id}",
                     headers={"X-API-Key": auditor}, json={"enabled": False})
    assert r.status_code == 403
    r = client.patch(f"/api/console/detection-rules/{rule.id}",
                    headers={"X-API-Key": writer}, json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    # 重新启用
    r = client.patch(f"/api/console/detection-rules/{rule.id}",
                     headers={"X-API-Key": writer}, json={"enabled": True})
    assert r.json()["enabled"] is True


def test_rule_toggle_404(client, db_session):
    admin = make_user(db_session, "ad@example.com", "admin")
    r = client.patch("/api/console/detection-rules/99999",
                     headers={"X-API-Key": admin}, json={"enabled": False})
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"
