"""认证 API 测试：注册 / 登录轮换 / X-API-Key / RBAC 边界。"""
from tests.conftest import make_user


def test_register_guest_issues_api_key(client):
    r = client.post(
        "/api/auth/register",
        json={"email": "u1@example.com", "password": "long-enough-pw"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["role"] == "guest"
    assert body["api_key"].startswith("gk_")

    # 新 key 可用，/me 返回本人信息
    me = client.get("/api/auth/me", headers={"X-API-Key": body["api_key"]})
    assert me.status_code == 200
    assert me.json()["email"] == "u1@example.com"
    assert me.json()["role"] == "guest"


def test_register_duplicate_email_conflict_envelope(client):
    payload = {"email": "dup@example.com", "password": "long-enough-pw"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "CONFLICT"
    assert body["trace_id"]


def test_register_role_requires_admin(client):
    r = client.post(
        "/api/auth/register",
        json={
            "email": "op@example.com",
            "password": "long-enough-pw",
            "role": "operator",
        },
    )
    assert r.status_code == 403
    assert r.json()["code"] == "PERMISSION_DENIED"


def test_invalid_api_key_401_envelope(client):
    r = client.get("/api/auth/me", headers={"X-API-Key": "gk_bogus"})
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "UNAUTHENTICATED"
    assert body["trace_id"]


def test_login_rotates_api_key(client, db_session):
    client.post(
        "/api/auth/register",
        json={"email": "u2@example.com", "password": "long-enough-pw"},
    )
    r = client.post(
        "/api/auth/login",
        json={"email": "u2@example.com", "password": "long-enough-pw"},
    )
    assert r.status_code == 200
    new_key = r.json()["api_key"]
    assert client.get("/api/auth/me", headers={"X-API-Key": new_key}).status_code == 200


def test_login_wrong_password_404(client, db_session):
    client.post(
        "/api/auth/register",
        json={"email": "u3@example.com", "password": "long-enough-pw"},
    )
    r = client.post(
        "/api/auth/login",
        json={"email": "u3@example.com", "password": "wrong-password"},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


def test_trace_id_header_on_success(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.headers.get("X-Trace-Id")


def test_admin_can_register_operator(client, db_session):
    admin_key = make_user(db_session, "admin@example.com", "admin")
    r = client.post(
        "/api/auth/register",
        json={
            "email": "op@example.com",
            "password": "long-enough-pw",
            "role": "operator",
        },
        headers={"X-API-Key": admin_key},
    )
    assert r.status_code == 201
    assert r.json()["role"] == "operator"
