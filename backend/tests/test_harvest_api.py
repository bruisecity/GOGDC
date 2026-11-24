"""采集任务控制台 API 测试：CRUD + 状态机 + RBAC。"""
from tests.conftest import make_user


def _ids(db):
    from app.models import DataSource, Node

    ds = db.query(DataSource).filter(DataSource.code == "usgs").one()
    node = db.query(Node).filter(Node.code == "node-1").one()
    return ds.id, node.id


def test_operator_can_create_and_list(client, db_session):
    key = make_user(db_session, "op@example.com", "operator")
    ds_id, node_id = _ids(db_session)
    r = client.post(
        "/api/console/harvest-tasks",
        json={"data_source_id": ds_id, "node_id": node_id},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 201
    task = r.json()
    assert task["status"] == "idle"
    assert task["retry_count"] == 0 and task["retry_max"] == 3

    lst = client.get("/api/console/harvest-tasks", headers={"X-API-Key": key})
    assert lst.status_code == 200
    assert lst.json()["total"] >= 1
    assert lst.json()["items"][0]["id"] == task["id"]


def test_create_with_unknown_source_404(client, db_session):
    key = make_user(db_session, "op@example.com", "operator")
    _, node_id = _ids(db_session)
    r = client.post(
        "/api/console/harvest-tasks",
        json={"data_source_id": 99999, "node_id": node_id},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


def test_patch_state_machine_and_envelope(client, db_session):
    key = make_user(db_session, "op@example.com", "operator")
    ds_id, node_id = _ids(db_session)
    task = client.post(
        "/api/console/harvest-tasks",
        json={"data_source_id": ds_id, "node_id": node_id},
        headers={"X-API-Key": key},
    ).json()

    # idle -> running 合法
    r = client.patch(
        f"/api/console/harvest-tasks/{task['id']}",
        json={"status": "running"},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "running"

    # running -> idle 合法（回位）
    r1 = client.patch(
        f"/api/console/harvest-tasks/{task['id']}",
        json={"status": "idle"},
        headers={"X-API-Key": key},
    )
    assert r1.status_code == 200

    # idle -> failed 非法（状态机拒绝，422 信封）
    r2 = client.patch(
        f"/api/console/harvest-tasks/{task['id']}",
        json={"status": "failed"},
        headers={"X-API-Key": key},
    )
    assert r2.status_code == 422
    body = r2.json()
    assert body["code"] == "INVALID_STATE_TRANSITION"
    assert body["trace_id"]

    # 不存在的任务 404
    r3 = client.patch(
        "/api/console/harvest-tasks/424242",
        json={"status": "idle"},
        headers={"X-API-Key": key},
    )
    assert r3.status_code == 404


def test_rbac_matrix_on_harvest(client, db_session):
    ds_id, node_id = _ids(db_session)
    # 匿名：读拒绝
    assert client.get("/api/console/harvest-tasks").status_code == 403
    # guest：无 harvest 任何权限
    guest_key = make_user(db_session, "g@example.com", "guest")
    assert (
        client.get(
            "/api/console/harvest-tasks", headers={"X-API-Key": guest_key}
        ).status_code
        == 403
    )
    # auditor：只读，写拒绝
    auditor_key = make_user(db_session, "a@example.com", "auditor")
    assert (
        client.get(
            "/api/console/harvest-tasks", headers={"X-API-Key": auditor_key}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/console/harvest-tasks",
            json={"data_source_id": ds_id, "node_id": node_id},
            headers={"X-API-Key": auditor_key},
        ).status_code
        == 403
    )
