"""W7 总览面板 API 测试：指标聚合 / 四类失败计数 / 在线率 / 水位 / RBAC。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import (
    Dataset,
    DatasetCategory,
    DataSource,
    HarvestTask,
    Node,
    StorageBucket,
)

from tests.conftest import make_user

NOW = datetime.now(timezone.utc)


def _seed(db):
    node1 = db.query(Node).filter(Node.code == "node-1").one()
    usgs = db.query(DataSource).filter(DataSource.code == "usgs").one()
    # 数据集 4 条：三类安全状态 + 1 条隔离；总量 1000 字节
    cat = DatasetCategory(code="dem", name="数字高程")
    db.add(cat)
    db.commit()
    for i, (status_, size) in enumerate(
        [("检测通过", 300), ("待检测", 200), ("检测中", 100), ("已隔离", 400)]
    ):
        db.add(
            Dataset(
                category_id=cat.id, format="GeoTIFF", size_bytes=size,
                security_status=status_, storage_key=f"load/{i}.tif",
            )
        )
    # 源健康度：usgs=healthy；新增 gbif=degraded
    gbif = DataSource(code="gbif", base_url="https://api.gbif.org",
                      health_status="degraded")
    db.add(gbif)
    db.commit()
    # 任务：3 条 idle + 1 failed（凭证失效）+ 1 paused
    for src, node, status_, cls in [
        (usgs, node1, "idle", None),
        (usgs, node1, "idle", "连接超时"),   # 可重试失败后回 idle，仍带失败类别
        (gbif, node1, "failed", "凭证失效"),
        (gbif, node1, "paused", None),
    ]:
        db.add(HarvestTask(data_source_id=src.id, node_id=node.id,
                           status=status_, last_failure_class=cls))
    # 心跳：node-1 在线（60s 前）
    node1.heartbeat_at = NOW - timedelta(seconds=60)
    # 存储桶：80MB/100MB -> 告警态（0.8 告警线）
    db.add(StorageBucket(name="geodata-hot", tier="热",
                         quota_bytes=100 * 1024 * 1024, used_bytes=80 * 1024 * 1024))
    db.commit()


def test_overview_aggregates_all_panels(db_session, client):
    key = make_user(db_session, "admin@example.com", "admin")
    _seed(db_session)
    r = client.get("/api/console/overview", headers={"X-API-Key": key})
    assert r.status_code == 200
    body = r.json()

    assert body["datasets"]["total"] == 4
    assert body["datasets"]["total_bytes"] == 1000
    assert body["datasets"]["by_security_status"] == {
        "待检测": 1, "检测中": 1, "检测通过": 1, "已隔离": 1,
    }
    assert body["dataset_categories"] == 1

    assert body["nodes"] == {"total": 1, "online": 1, "online_rate": 1.0}

    assert body["data_sources"]["by_health"] == {"healthy": 1, "degraded": 1, "down": 0}
    codes = {i["code"]: i for i in body["data_sources"]["items"]}
    assert codes["gbif"]["health_status"] == "degraded"

    assert body["harvest_tasks"]["total"] == 4
    assert body["harvest_tasks"]["by_status"] == {
        "idle": 2, "running": 0, "failed": 1, "paused": 1,
    }
    # 四类失败计数：恒四键
    assert body["harvest_failures"] == {
        "连接超时": 1, "源站限流": 0, "凭证失效": 1, "元数据校验": 0,
    }

    st = body["storage"]
    assert st["alert_ratio"] == 0.8
    assert st["total_quota_bytes"] == 100 * 1024 * 1024
    assert st["total_used_bytes"] == 80 * 1024 * 1024
    assert st["buckets"][0]["status"] == "告警"
    assert st["buckets"][0]["ratio"] == 0.8


def test_overview_empty_db_has_all_keys(db_session, client):
    """空库不缺键：四类失败 / 任务状态 / 健康度四键恒存在（面板不空指针）。"""
    key = make_user(db_session, "op@example.com", "operator")
    r = client.get("/api/console/overview", headers={"X-API-Key": key})
    assert r.status_code == 200
    body = r.json()
    assert body["datasets"]["total"] == 0
    assert body["nodes"] == {"total": 1, "online": 0, "online_rate": 0.0}
    assert body["harvest_failures"] == {
        "连接超时": 0, "源站限流": 0, "凭证失效": 0, "元数据校验": 0,
    }
    assert body["harvest_tasks"]["by_status"] == {
        "idle": 0, "running": 0, "failed": 0, "paused": 0,
    }
    assert body["storage"]["buckets"] == []


def test_overview_rbac(db_session, client):
    # guest 无 overview:读 -> 403；匿名同样 403
    gk = make_user(db_session, "g@example.com", "guest")
    assert client.get("/api/console/overview", headers={"X-API-Key": gk}).status_code == 403
    assert client.get("/api/console/overview").status_code == 403
    # researcher 只读可用
    rk = make_user(db_session, "r@example.com", "researcher")
    assert client.get("/api/console/overview", headers={"X-API-Key": rk}).status_code == 200


def test_online_rate_partial(db_session, client):
    key = make_user(db_session, "a@example.com", "admin")
    node1 = db_session.query(Node).filter(Node.code == "node-1").one()
    node1.heartbeat_at = NOW - timedelta(seconds=30)
    db_session.add(Node(code="node-2", org_name="承担单位B"))
    db_session.commit()
    body = client.get("/api/console/overview", headers={"X-API-Key": key}).json()
    assert body["nodes"] == {"total": 2, "online": 1, "online_rate": 0.5}
