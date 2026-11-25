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


# ---- webhook 送达与重试集成（W5 补齐） ----

from app.services.alert_service import WebhookDispatcher  # noqa: E402


class _FailingTransport:
    def __init__(self, status=500):
        self.status = status
        self.calls = 0

    def request(self, method, url, headers, body):
        self.calls += 1
        return self.status, b""


class _OkTransport:
    def __init__(self):
        self.calls = 0

    def request(self, method, url, headers, body):
        self.calls += 1
        return 200, b""


def test_webhook_success_attached_to_alert_audit(db_session):
    _bucket(db_session, "hot", used=900, quota=1000)
    t = _OkTransport()
    disp = WebhookDispatcher(["http://hook.example/a"], transport=t,
                             sleep=lambda *_: None)

    alerts = check_watermarks(db_session, webhook_urls=["http://hook.example/a"],
                              dispatcher=disp)

    assert [a["bucket"] for a in alerts] == ["hot"]
    assert t.calls == 1
    row = db_session.query(AuditLog).filter(AuditLog.operation == "\u5bb9\u91cf\u6c34\u4f4d").one()
    assert row.detail["webhook"] == [
        {"url": "http://hook.example/a", "result": "\u6210\u529f", "attempts": 1}
    ]
    assert row.detail["bucket"] == "hot"  # 快照字段仍在


def test_webhook_failure_does_not_lose_alert(db_session):
    """webhook 全路失败：告警审计照落（可达兜底），失败明细进 detail。"""
    _bucket(db_session, "hot", used=900, quota=1000)
    t = _FailingTransport(500)
    disp = WebhookDispatcher(["http://hook.example/a", "http://hook.example/b"],
                             transport=t, max_attempts=2, sleep=lambda *_: None)

    alerts = check_watermarks(db_session,
                              webhook_urls=["http://hook.example/a", "http://hook.example/b"],
                              dispatcher=disp)

    assert alerts != []
    row = db_session.query(AuditLog).filter(AuditLog.operation == "\u5bb9\u91cf\u6c34\u4f4d").one()
    assert row.result == "\u544a\u8b66"  # 审计兜底：告警仍可达
    assert len(row.detail["webhook"]) == 2
    assert all(w["result"] == "\u5931\u8d25" and w["attempts"] == 2
               for w in row.detail["webhook"])
    assert t.calls == 4  # 2 路 × 2 次重试


def test_webhook_recovery_event_also_delivered(db_session):
    b = _bucket(db_session, "hot", used=900, quota=1000)
    t_ok = _OkTransport()
    check_watermarks(db_session, webhook_urls=["http://hook.example/a"],
                     dispatcher=WebhookDispatcher(["http://hook.example/a"],
                                                  transport=t_ok, sleep=lambda *_: None))
    b.used_bytes = 500
    db_session.commit()
    t2 = _OkTransport()
    check_watermarks(db_session, webhook_urls=["http://hook.example/a"],
                     dispatcher=WebhookDispatcher(["http://hook.example/a"],
                                                  transport=t2, sleep=lambda *_: None))
    assert t2.calls == 1  # 恢复事件同样送达
    rows = db_session.query(AuditLog).filter(
        AuditLog.operation == "\u5bb9\u91cf\u6c34\u4f4d").order_by(AuditLog.id).all()
    assert [r.result for r in rows] == ["\u544a\u8b66", "\u6062\u590d"]


def test_webhook_disabled_by_default_and_explicit_empty(db_session):
    """未配置 URL（默认）：不推送、审计 detail 无 webhook 字段。"""
    _bucket(db_session, "hot", used=900, quota=1000)
    check_watermarks(db_session)  # webhook_urls=None → settings 默认（测试环境为空）
    row = db_session.query(AuditLog).filter(AuditLog.operation == "\u5bb9\u91cf\u6c34\u4f4d").one()
    assert "webhook" not in row.detail

    # 幂等：仍处告警态 → 不重复落审计（webhook 不重发）
    check_watermarks(db_session, webhook_urls=[])
    assert db_session.query(AuditLog).filter(
        AuditLog.operation == "\u5bb9\u91cf\u6c34\u4f4d").count() == 1


def test_no_state_change_no_webhook(db_session):
    _bucket(db_session, "ok", used=100, quota=1000)
    t = _OkTransport()
    check_watermarks(db_session, webhook_urls=["http://hook.example/a"],
                     dispatcher=WebhookDispatcher(["http://hook.example/a"],
                                                  transport=t, sleep=lambda *_: None))
    assert t.calls == 0  # 水位正常无状态变化：不推送
