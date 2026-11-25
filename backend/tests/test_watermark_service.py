"""容量水位监控测试（W5 切片 C：80% 告警线 + 告警可达 + 幂等去重）。"""
from __future__ import annotations

from app.models import AuditLog, StorageBucket
from app.services.watermark_service import (
    bucket_status,
    check_watermarks,
)


def _bucket(db, name, used, quota, tier="热"):
    b = StorageBucket(name=name, tier=tier, quota_bytes=quota, used_bytes=used)
    db.add(b)
    db.commit()
    return b


def test_bucket_status_boundaries(db_session):
    no_quota = _bucket(db_session, "b0", used=500, quota=0)
    below = _bucket(db_session, "b1", used=790, quota=1000)
    at_line = _bucket(db_session, "b2", used=800, quota=1000)
    assert bucket_status(no_quota)["status"] == "未设配额"
    assert bucket_status(no_quota)["ratio"] is None
    assert bucket_status(below)["status"] == "正常"
    assert bucket_status(below)["ratio"] == 0.79
    assert bucket_status(at_line)["status"] == "告警"  # 恰到 80% 即告警
    assert bucket_status(at_line)["ratio"] == 0.8


def test_check_watermarks_writes_alert_audit_once(db_session):
    _bucket(db_session, "hot", used=900, quota=1000)

    alerts = check_watermarks(db_session)
    alerts_again = check_watermarks(db_session)  # 仍处告警态 → 不重复告警

    assert [a["bucket"] for a in alerts] == ["hot"]
    assert [a["bucket"] for a in alerts_again] == ["hot"]
    rows = db_session.query(AuditLog).filter(AuditLog.operation == "容量水位").all()
    assert len(rows) == 1
    assert rows[0].result == "告警"
    assert rows[0].detail["ratio"] == 0.9
    assert rows[0].detail["bucket"] == "hot"
    assert rows[0].resource == "storage"


def test_check_watermarks_recovery_and_realert(db_session):
    b = _bucket(db_session, "hot", used=900, quota=1000)
    check_watermarks(db_session)  # 告警

    b.used_bytes = 500
    db_session.commit()
    alerts = check_watermarks(db_session)  # 回落 → 恢复
    assert alerts == []
    b.used_bytes = 850
    db_session.commit()
    check_watermarks(db_session)  # 再次越线 → 新告警

    rows = db_session.query(AuditLog).filter(
        AuditLog.operation == "容量水位").order_by(AuditLog.id).all()
    assert [r.result for r in rows] == ["告警", "恢复", "告警"]


def test_check_watermarks_normal_bucket_silent(db_session):
    _bucket(db_session, "ok", used=100, quota=1000)
    alerts = check_watermarks(db_session)
    assert alerts == []
    assert db_session.query(AuditLog).filter(
        AuditLog.operation == "容量水位").count() == 0
