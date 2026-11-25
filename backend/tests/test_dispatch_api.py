"""调度控制台 API 测试（W6）：策略校验、手动派发、调度状态、RBAC。"""
from datetime import datetime, timedelta

import pytest

from app.models import AuditLog, DataSource, HarvestTask, Node
from tests.conftest import make_user
from tests.test_dispatch_service import _seed_sources, _task

BASE = "/api/console/harvest-tasks"


@pytest.fixture()
def operator(db_session):
    return make_user(db_session, "op@example.com", "operator")


@pytest.fixture()
def auditor(db_session):
    return make_user(db_session, "au@example.com", "auditor")


def _headers(key):
    return {"X-API-Key": key}


def test_create_task_with_time_window(client, db_session, operator):
    sources = _seed_sources(db_session)
    node = db_session.query(Node).filter(Node.code == "node-1").one()
    resp = client.post(
        BASE,
        headers=_headers(operator),
        json={
            "data_source_id": sources["gbif"].id,
            "node_id": node.id,
            "schedule_cron": "0 */6 * * *",
            "time_window": "09:00-18:00",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["time_window"] == "09:00-18:00"
    assert body["last_run_at"] is None


@pytest.mark.parametrize("cron", ["0 9 * *", "99 9 * * *"])
def test_create_task_rejects_bad_cron(client, db_session, operator, cron):
    sources = _seed_sources(db_session)
    node = db_session.query(Node).filter(Node.code == "node-1").one()
    resp = client.post(
        BASE, headers=_headers(operator),
        json={"data_source_id": sources["usgs"].id, "node_id": node.id, "schedule_cron": cron},
    )
    assert resp.status_code == 422
    assert "schedule_cron" in resp.json()["detail"] or "cron" in resp.json()["detail"]


@pytest.mark.parametrize("window", ["18:00-09:00", "9-18", "aa:bb-cc:dd"])
def test_create_task_rejects_bad_time_window(client, db_session, operator, window):
    sources = _seed_sources(db_session)
    node = db_session.query(Node).filter(Node.code == "node-1").one()
    resp = client.post(
        BASE, headers=_headers(operator),
        json={"data_source_id": sources["usgs"].id, "node_id": node.id, "time_window": window},
    )
    assert resp.status_code == 422


def test_dispatch_status_reports_active_sources(client, db_session, operator):
    _seed_sources(db_session)
    _task(db_session, db_session.query(DataSource).filter(DataSource.code == "gbif").one(),
          last_run=datetime(2026, 11, 25, 8, 0))
    resp = client.get(f"{BASE}/dispatch/status", headers=_headers(operator))
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_sources"] == ["usgs", "gbif", "esa"]
    assert sorted(body["registered_adapters"]) == ["esa", "gbif", "usgs"]
    assert {s["code"] for s in body["sources"]} == {"usgs", "gbif", "esa"}


def test_dispatch_run_requires_write_role(client, db_session, auditor):
    resp = client.post(f"{BASE}/dispatch/run", headers=_headers(auditor), json={})
    assert resp.status_code == 403


def test_dispatch_run_with_no_due_tasks_is_noop(client, db_session, operator):
    _seed_sources(db_session)
    resp = client.post(f"{BASE}/dispatch/run", headers=_headers(operator), json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["considered"] == 0
    assert body["dispatched"] == 0
    # 无到期任务也落审计（可观测「调过一轮但没活」）
    audits = db_session.query(AuditLog).filter(AuditLog.operation == "采集调度").all()
    assert len(audits) == 1
    assert audits[0].detail["considered"] == 0


def test_dispatch_run_quota_skip_surfaces_in_response(client, db_session, operator):
    sources = _seed_sources(db_session)
    sources["esa"].quota_limit = 10
    sources["esa"].quota_used = 10
    db_session.commit()
    task = _task(db_session, sources["esa"], cron="")

    resp = client.post(
        f"{BASE}/dispatch/run", headers=_headers(operator), json={"task_ids": [task.id]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["skipped"] == 1
    assert body["dispatched"] == 0
    assert body["tasks"][0]["action"] == "skipped"
    assert "配额" in body["tasks"][0]["reason"]
