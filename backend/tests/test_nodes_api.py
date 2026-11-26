"""W7 汇聚节点面板 API 测试：列表在线判定 / 心跳上报 / RBAC / 审计。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import AuditLog, HarvestTask, Node

from tests.conftest import make_user

NOW = datetime.now(timezone.utc)


def _seed_node(db, code, org, *, heartbeat_at=None, egress=False):
    from app.models import Node

    node = Node(code=code, org_name=org, public_egress=egress, heartbeat_at=heartbeat_at)
    db.add(node)
    db.commit()
    return node


def test_nodes_list_requires_read_role(db_session, client):
    key = make_user(db_session, "op@example.com", "operator")
    r = client.get("/api/console/nodes", headers={"X-API-Key": key})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1          # conftest 种子 node-1
    assert body["online"] == 0         # 从未上报心跳 -> 离线
    assert body["items"][0]["code"] == "node-1"
    assert body["items"][0]["online"] is False


def test_nodes_list_rejects_guest(db_session, client):
    key = make_user(db_session, "g@example.com", "guest")
    r = client.get("/api/console/nodes", headers={"X-API-Key": key})
    assert r.status_code == 403


def test_nodes_list_anonymous_rejected(db_session, client):
    # 匿名主体无 node:读 权限 -> 403（与其它 console 端点口径一致）
    r = client.get("/api/console/nodes")
    assert r.status_code == 403


def test_nodes_list_online_judgement_and_task_count(db_session, client):
    key = make_user(db_session, "op@example.com", "operator")
    fresh = _seed_node(db_session, "node-2", "承担单位B",
                       heartbeat_at=NOW - timedelta(seconds=60), egress=True)
    stale = _seed_node(db_session, "node-3", "承担单位C",
                       heartbeat_at=NOW - timedelta(hours=1))
    # node-1 挂 1 个采集任务，node-2/3 无任务
    from app.models import DataSource
    src = db_session.query(DataSource).filter(DataSource.code == "usgs").one()
    node1 = db_session.query(Node).filter(Node.code == "node-1").one()
    db_session.add(HarvestTask(data_source_id=src.id, node_id=node1.id))
    db_session.commit()
    r = client.get("/api/console/nodes?limit=100", headers={"X-API-Key": key})
    assert r.status_code == 200
    items = {i["code"]: i for i in r.json()["items"]}
    assert items["node-2"]["online"] is True     # 60s 前心跳 -> 在线
    assert items["node-3"]["online"] is False    # 1h 前心跳 -> 离线
    assert items["node-1"]["online"] is False
    assert items["node-1"]["task_count"] == 1
    assert items["node-2"]["task_count"] == 0
    assert r.json()["online"] == 1


def test_heartbeat_updates_and_audits(db_session, client):
    key = make_user(db_session, "op@example.com", "operator")
    node = _seed_node(db_session, "node-9", "承担单位D")
    r = client.post(
        f"/api/console/nodes/{node.id}/heartbeat",
        headers={"X-API-Key": key},
        json={"disk_total": 1000, "disk_used": 400, "download_bytes_total": 555},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["online"] is True
    db_session.refresh(node)
    assert node.disk_total == 1000
    assert node.disk_used == 400
    assert node.download_bytes_total == 555
    assert node.heartbeat_at is not None
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.operation == "节点心跳")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert audit.detail["node_code"] == "node-9"
    assert audit.detail["download_bytes_total"] == 555


def test_heartbeat_requires_write_and_exists(db_session, client):
    read_key = make_user(db_session, "aud@example.com", "auditor")  # auditor: node 只读
    r = client.post(
        "/api/console/nodes/1/heartbeat",
        headers={"X-API-Key": read_key},
        json={"disk_total": 1},
    )
    assert r.status_code == 403

    write_key = make_user(db_session, "op@example.com", "operator")
    r = client.post(
        "/api/console/nodes/99999/heartbeat",
        headers={"X-API-Key": write_key},
        json={},
    )
    assert r.status_code == 404
