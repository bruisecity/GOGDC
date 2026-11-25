"""存储控制台 API 测试（W5 切片 C：RBAC + 水位 + 策略 + 迁移触发 + 告警可达）。"""
from __future__ import annotations

import datetime as dt

from app.main import app
from app.models import AuditLog, StorageBucket, StorageObject
from app.routes.deps import get_tier_stores
from tests.conftest import make_user

NOW = dt.datetime(2026, 12, 25, 12, 0, 0)
POLICY = {"transitions": [
    {"target_tier": "温", "bucket": "geodata-warm", "after_days": 30},
]}


def _use_stores(tier_stores):
    app.dependency_overrides[get_tier_stores] = lambda: tier_stores


def _seed_bucket(db, name="geodata-hot", tier="热", used=0, quota=0, policy=None):
    b = StorageBucket(name=name, tier=tier, quota_bytes=quota,
                      used_bytes=used, lifecycle_policy=policy or {})
    db.add(b)
    db.commit()
    return b


# ---- RBAC ----

def test_storage_endpoints_rbac_matrix(client, db_session, tier_stores):
    _use_stores(tier_stores)
    _seed_bucket(db_session)
    operator = make_user(db_session, "op@example.com", "operator")
    auditor = make_user(db_session, "au@example.com", "auditor")
    researcher = make_user(db_session, "re@example.com", "researcher")
    guest = make_user(db_session, "gu@example.com", "guest")

    # 读（operator / auditor 可，匿名/researcher/guest 403）
    for key in (operator, auditor):
        assert client.get("/api/console/storage/buckets",
                          headers={"X-API-Key": key}).status_code == 200
        assert client.get("/api/console/storage/watermark",
                          headers={"X-API-Key": key}).status_code == 200
    for key in (researcher, guest):
        assert client.get("/api/console/storage/buckets",
                          headers={"X-API-Key": key}).status_code == 403
    assert client.get("/api/console/storage/buckets").status_code == 403

    # 写（auditor 只读：写 403）
    assert client.post("/api/console/storage/watermark/check",
                       headers={"X-API-Key": auditor}).status_code == 403
    assert client.post("/api/console/storage/lifecycle/run",
                       headers={"X-API-Key": operator}).status_code == 200


# ---- 桶清单与水位快照 ----

def test_buckets_list_carries_watermark(client, db_session):
    _seed_bucket(db_session, "hot", used=900, quota=1000)
    _seed_bucket(db_session, "warm", tier="温", used=100, quota=1000)
    key = make_user(db_session, "q@example.com", "operator")

    r = client.get("/api/console/storage/buckets", headers={"X-API-Key": key})

    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    by_name = {b["name"]: b for b in body["items"]}
    assert by_name["hot"]["watermark_status"] == "告警"
    assert by_name["hot"]["watermark_ratio"] == 0.9
    assert by_name["warm"]["watermark_status"] == "正常"


def test_watermark_endpoints(client, db_session):
    _seed_bucket(db_session, "hot", used=900, quota=1000)
    key = make_user(db_session, "w@example.com", "operator")

    r = client.get("/api/console/storage/watermark", headers={"X-API-Key": key})
    assert r.json()["alert_ratio"] == 0.8
    assert [b["status"] for b in r.json()["buckets"]] == ["告警"]

    r = client.post("/api/console/storage/watermark/check", headers={"X-API-Key": key})
    assert r.status_code == 200
    body = r.json()
    assert body["audited"] is True
    assert body["alerts"][0]["bucket"] == "hot"


def test_watermark_alert_reachable_via_audit_console(client, db_session):
    """验收链路：水位巡检落告警 → 审计控制台按 operation 检索到该告警。"""
    _seed_bucket(db_session, "hot", used=900, quota=1000)
    op = make_user(db_session, "op2@example.com", "operator")
    auditor = make_user(db_session, "au2@example.com", "auditor")
    client.post("/api/console/storage/watermark/check", headers={"X-API-Key": op})

    r = client.get("/api/console/audit-logs?operation=容量水位",
                   headers={"X-API-Key": auditor})

    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["result"] == "告警"
    assert items[0]["detail"]["bucket"] == "hot"


# ---- 策略配置 ----

def test_policy_set_and_validate(client, db_session):
    bucket = _seed_bucket(db_session)
    key = make_user(db_session, "p@example.com", "operator")

    r = client.post(f"/api/console/storage/buckets/{bucket.id}/policy",
                    json=POLICY, headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.json()["lifecycle_policy"] == POLICY
    db_session.refresh(bucket)
    assert bucket.lifecycle_policy == POLICY
    assert db_session.query(AuditLog).filter(
        AuditLog.operation == "策略配置").count() == 1

    # 向上迁移的策略被 422 拒绝
    r = client.post(f"/api/console/storage/buckets/{bucket.id}/policy",
                    json={"transitions": [
                        {"target_tier": "热", "bucket": "x", "after_days": 1}]},
                    headers={"X-API-Key": key})
    assert r.status_code == 422

    r = client.post("/api/console/storage/buckets/9999/policy",
                    json=POLICY, headers={"X-API-Key": key})
    assert r.status_code == 404


# ---- 迁移触发 ----

def test_lifecycle_run_endpoint_migrates(client, db_session, tier_stores):
    _use_stores(tier_stores)
    hot = _seed_bucket(db_session, used=400)
    obj = StorageObject(bucket_id=hot.id, key="usgs/1/a.tif", size_bytes=400,
                        last_access_at=dt.datetime(2026, 10, 1))  # 已闲置 50+ 天（真实时钟口径）
    db_session.add(obj)
    db_session.commit()
    tier_stores.store_for("热").put("usgs/1/a.tif", b"a" * 400)

    # 先经 API 配置策略，再触发迁移
    op = make_user(db_session, "run@example.com", "operator")
    assert client.post(f"/api/console/storage/buckets/{hot.id}/policy",
                       json=POLICY, headers={"X-API-Key": op}).status_code == 200
    r = client.post("/api/console/storage/lifecycle/run", headers={"X-API-Key": op})

    assert r.status_code == 200
    assert r.json()["migrated"] == 1
    assert r.json()["transitions"] == {"热->温": 1}
    db_session.refresh(obj)
    warm = db_session.query(StorageBucket).filter_by(name="geodata-warm").one()
    assert obj.bucket_id == warm.id
    assert tier_stores.store_for("温").get("usgs/1/a.tif") == b"a" * 400
    assert not tier_stores.store_for("热").exists("usgs/1/a.tif")
