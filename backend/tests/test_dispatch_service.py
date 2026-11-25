"""调度服务测试（W6）：3 源并行采集、配额前置、健康度、四类失败统计。"""
from datetime import datetime, timedelta
import threading
import time

import pytest

from app.models import AuditLog, DataSource, HarvestTask, Node
from app.services.dispatch_service import (
    due_tasks,
    quota_exhausted,
    record_health,
    run_dispatch_round,
)
from app.services.sources import build_adapter
from app.services.usgs import HarvestError

from tests.test_sources_adapters import ESA_PAYLOAD, GBIF_PAYLOAD
from tests.test_usgs_adapter import FakeTransport, GOOD_PAYLOAD

NOW = datetime(2026, 12, 28, 9, 5)


class RecordingTransport:
    """按源返回固定 payload，并记录并发度（供并行断言）。"""

    def __init__(self, payloads, *, statuses=None, delays=None):
        self.payloads = payloads
        self.statuses = statuses or {}
        self.delays = delays or {}
        self.calls = []
        self.lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0

    def request(self, method, url, params):
        code = next(k for k in self.payloads if k in url or self._match(k, url))
        with self.lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            time.sleep(self.delays.get(code, 0))
            self.calls.append((code, method, url, params))
            return self.statuses.get(code, 200), self.payloads[code]
        finally:
            with self.lock:
                self.inflight -= 1

    @staticmethod
    def _match(code, url):
        if code == "usgs":
            return "nationalmap" in url
        if code == "gbif":
            return "gbif" in url
        if code == "esa":
            return "copernicus" in url
        return False


def _seed_sources(db):
    """确保三源（usgs/gbif/esa）都在库中。"""
    existing = {s.code: s for s in db.query(DataSource).all()}
    for code, base in (
        ("gbif", "https://api.gbif.org"),
        ("esa", "https://catalogue.dataspace.copernicus.eu"),
    ):
        if code not in existing:
            db.add(DataSource(code=code, base_url=base))
    db.commit()
    return {s.code: s for s in db.query(DataSource).all()}


def _task(db, source, *, cron="0 * * * *", window="", last_run=None, status="idle", retry_max=3):
    node = db.query(Node).filter(Node.code == "node-1").one()
    task = HarvestTask(
        data_source_id=source.id,
        node_id=node.id,
        schedule_cron=cron,
        time_window=window,
        retry_max=retry_max,
        retry_count=0,
        status=status,
        last_run_at=last_run,
    )
    db.add(task)
    db.commit()
    return task


def _factory(payloads, **kw):
    transport = RecordingTransport(payloads, **kw)
    payloads_by_code = payloads

    def factory(code):
        return build_adapter(code, transport)

    factory.transport = transport
    return factory


def test_quota_precheck():
    src = DataSource(code="x", base_url="u", quota_limit=100, quota_used=100)
    assert quota_exhausted(src) is True
    src.quota_used = 99
    assert quota_exhausted(src) is False
    src.quota_limit = 0
    src.quota_used = 10 ** 9
    assert quota_exhausted(src) is False   # 0 = 不限量


def test_due_tasks_picks_only_active_sources_and_due(db_session):
    sources = _seed_sources(db_session)
    in_window = _task(db_session, sources["usgs"], last_run=NOW - timedelta(hours=1))
    manual = _task(db_session, sources["gbif"], cron="")
    wrong_window = _task(db_session, sources["esa"], window="20:00-23:00", last_run=NOW - timedelta(hours=1))
    # 非在役源（WorldPop 延至试运行期）
    db_session.add(DataSource(code="worldpop", base_url="https://api.worldpop.org"))
    db_session.commit()
    wp = _task(
        db_session, db_session.query(DataSource).filter(DataSource.code == "worldpop").one(),
        last_run=NOW - timedelta(hours=1),
    )

    due = due_tasks(db_session, now=NOW)
    ids = {t.id for t in due}
    assert in_window.id in ids
    assert manual.id not in ids        # cron 空 = 仅手动
    assert wrong_window.id not in ids  # 限流时段外
    assert wp.id not in ids            # 不在 ACTIVE_SOURCES


def test_dispatching_three_sources_marks_success_and_health(db_session):
    sources = _seed_sources(db_session)
    tasks = {
        code: _task(db_session, sources[code], last_run=NOW - timedelta(hours=1))
        for code in ("usgs", "gbif", "esa")
    }
    factory = _factory(
        {"usgs": GOOD_PAYLOAD, "gbif": GBIF_PAYLOAD, "esa": ESA_PAYLOAD},
        delays={"usgs": 0.05, "gbif": 0.05, "esa": 0.05},
    )
    result = run_dispatch_round(db_session, factory, max_items=1, now=NOW)

    assert result.considered == 3
    assert result.dispatched == 3
    assert result.succeeded == 3
    assert result.failed == 0
    # 并行度：三源同时 inflight
    assert factory.transport.max_inflight >= 2

    for code, task in tasks.items():
        db_session.refresh(task)
        assert task.status == "idle"        # 成功回 idle 等下一轮
        assert task.last_run_at == NOW
        assert task.retry_count == 0
        src = db_session.get(DataSource, task.data_source_id)
        db_session.refresh(src)
        assert src.health_status == "healthy"
        assert src.last_success_at == NOW

    # 汇总审计：四类失败计数全 0
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.operation == "采集调度")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit.detail["succeeded"] == 3
    assert audit.detail["failure_classes"] == {
        "连接超时": 0, "源站限流": 0, "凭证失效": 0, "元数据校验": 0
    }
    assert len(audit.detail["tasks"]) == 3


def test_retryable_failure_degrades_and_schedules_retry(db_session):
    sources = _seed_sources(db_session)
    task = _task(db_session, sources["gbif"], last_run=NOW - timedelta(hours=1), retry_max=3)
    factory = _factory({"gbif": None}, statuses={"gbif": 429})
    result = run_dispatch_round(db_session, factory, now=NOW, task_ids=[task.id])

    assert result.retry_scheduled == 1
    assert result.count("源站限流") == 1
    db_session.refresh(task)
    assert task.status == "idle"
    assert task.retry_count == 1
    assert task.last_failure_class == "源站限流"
    src = db_session.get(DataSource, task.data_source_id)
    db_session.refresh(src)
    assert src.health_status == "degraded"
    assert src.last_success_at is None     # 降级不记成功时刻


def test_non_retryable_failure_marks_down(db_session):
    sources = _seed_sources(db_session)
    task = _task(db_session, sources["esa"], last_run=NOW - timedelta(hours=1))
    factory = _factory({"esa": None}, statuses={"esa": 401})
    result = run_dispatch_round(db_session, factory, now=NOW, task_ids=[task.id])

    assert result.failed == 1
    assert result.count("凭证失效") == 1
    db_session.refresh(task)
    assert task.status == "failed"
    src = db_session.get(DataSource, task.data_source_id)
    db_session.refresh(src)
    assert src.health_status == "down"


def test_retry_exhausted_marks_down(db_session):
    sources = _seed_sources(db_session)
    task = _task(db_session, sources["usgs"], last_run=NOW - timedelta(hours=1), retry_max=0)
    factory = _factory({"usgs": None}, statuses={"usgs": 503})
    result = run_dispatch_round(db_session, factory, now=NOW, task_ids=[task.id])
    assert result.failed == 1
    db_session.refresh(task)
    assert task.status == "failed"
    src = db_session.get(DataSource, task.data_source_id)
    db_session.refresh(src)
    assert src.health_status == "down"


def test_quota_exhausted_skips_without_request(db_session):
    sources = _seed_sources(db_session)
    sources["gbif"].quota_limit = 1000
    sources["gbif"].quota_used = 1000
    db_session.commit()
    task = _task(db_session, sources["gbif"], last_run=NOW - timedelta(hours=1))
    factory = _factory({"gbif": GBIF_PAYLOAD})
    result = run_dispatch_round(db_session, factory, now=NOW, task_ids=[task.id])

    assert result.considered == 1
    assert result.skipped == 1
    assert result.dispatched == 0
    assert factory.transport.calls == []          # 未向源站发请求
    db_session.refresh(task)
    assert task.status == "idle"                  # 状态未被占位

    skip_audits = [
        a for a in db_session.query(AuditLog).filter(AuditLog.operation == "采集调度").all()
        if a.detail.get("reason") == "quota_exhausted"
    ]
    assert len(skip_audits) == 1
    assert skip_audits[0].detail["quota_used"] == 1000


def test_manual_trigger_ignores_cron_but_honors_status(db_session):
    sources = _seed_sources(db_session)
    manual = _task(db_session, sources["usgs"], cron="", last_run=NOW)
    paused = _task(db_session, sources["gbif"], cron="", status="paused")
    factory = _factory({"usgs": GOOD_PAYLOAD})
    result = run_dispatch_round(
        db_session, factory, now=NOW, task_ids=[manual.id, paused.id]
    )
    assert result.dispatched == 1      # paused 任务不跑
    assert result.succeeded == 1
    db_session.refresh(manual)
    assert manual.last_run_at == NOW


def test_one_source_failure_does_not_block_others(db_session):
    """单源故障不得拖垮其余源：3 源里 1 源 500，其余仍成功。"""
    sources = _seed_sources(db_session)
    tasks = {
        code: _task(db_session, sources[code], last_run=NOW - timedelta(hours=1))
        for code in ("usgs", "gbif", "esa")
    }
    factory = _factory(
        {"usgs": GOOD_PAYLOAD, "gbif": None, "esa": ESA_PAYLOAD},
        statuses={"gbif": 500},
    )
    result = run_dispatch_round(db_session, factory, now=NOW, max_items=1)
    assert result.succeeded == 2
    assert result.retry_scheduled == 1
    assert result.count("连接超时") == 1
    for code in ("usgs", "esa"):
        db_session.refresh(tasks[code])
        assert tasks[code].status == "idle"
    db_session.refresh(tasks["gbif"])
    assert tasks["gbif"].retry_count == 1


def test_record_health_transitions():
    src = DataSource(code="gbif", base_url="u")
    from app.services.harvest_service import HarvestRunResult

    ok = HarvestRunResult(task_id=1, outcome="succeeded", items=2, retry_count=0, failure_class=None)
    assert record_health(src, ok, now=NOW) == "healthy"
    assert src.last_success_at == NOW

    retry = HarvestRunResult(task_id=1, outcome="retry_scheduled", items=0, retry_count=1,
                             failure_class="源站限流")
    assert record_health(src, retry, now=NOW) == "degraded"

    dead = HarvestRunResult(task_id=1, outcome="failed", items=0, retry_count=3,
                            failure_class="凭证失效")
    assert record_health(src, dead, now=NOW) == "down"
