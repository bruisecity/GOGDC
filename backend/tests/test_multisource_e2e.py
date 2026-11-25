"""3 源并行采集端到端（W6 验收口径：3 源并行采集稳定运行）。

链路：到期任务 → 并行拉取（三源各自契约）→ 状态机落地 → 健康度 →
审计汇总（四类失败计数）。全部离线（假 transport 承载真实响应结构）。
"""
from datetime import datetime, timedelta

from app.models import AuditLog, DataSource, HarvestTask, Node
from app.services.dispatch_service import run_dispatch_round
from app.services.sources import build_adapter

from tests.test_dispatch_service import RecordingTransport, _seed_sources, _task
from tests.test_sources_adapters import ESA_PAYLOAD, GBIF_PAYLOAD
from tests.test_usgs_adapter import GOOD_PAYLOAD

NOW = datetime(2026, 12, 28, 10, 0)   # W6 当周工作日上午（限流时段内）


def _factory(payloads, **kw):
    transport = RecordingTransport(payloads, **kw)

    def factory(code):
        return build_adapter(code, transport)

    factory.transport = transport
    return factory


def test_three_sources_run_concurrently_and_land_state(db_session):
    sources = _seed_sources(db_session)
    node = db_session.query(Node).filter(Node.code == "node-1").one()
    tasks = {}
    for code, cron in (("usgs", "0 */2 * * *"), ("gbif", "0 */2 * * *"), ("esa", "*/30 * * * *")):
        tasks[code] = HarvestTask(
            data_source_id=sources[code].id,
            node_id=node.id,
            schedule_cron=cron,
            time_window="09:00-18:00",   # 限流时段：仅工作时间出网
            retry_max=3,
            retry_count=0,
            status="idle",
            last_run_at=NOW - timedelta(hours=2),
        )
    db_session.add_all(tasks.values())
    db_session.commit()

    factory = _factory(
        {"usgs": GOOD_PAYLOAD, "gbif": GBIF_PAYLOAD, "esa": ESA_PAYLOAD},
        delays={"usgs": 0.05, "gbif": 0.05, "esa": 0.05},
    )
    result = run_dispatch_round(db_session, factory, max_items=2, now=NOW)

    # 3 源并行：同时 inflight 至少 2（真实并发而非串行）
    assert factory.transport.max_inflight >= 2
    assert result.considered == 3
    assert result.dispatched == 3
    assert result.succeeded == 3
    assert result.skipped == 0

    # 每源都取到数据且条目符合该源契约
    by_code = {o.source_code: o for o in result.outcomes}
    assert by_code["usgs"].items == 1
    assert by_code["gbif"].items == 2
    assert by_code["esa"].items == 1
    assert by_code["gbif"].fetched[0].format == "JSON"
    assert by_code["esa"].fetched[0].format == "SENTINEL-2"
    assert by_code["usgs"].fetched[0].size_bytes == 12345

    # 状态机：成功回 idle、last_run_at 推进、重试清零
    for task in tasks.values():
        db_session.refresh(task)
        assert task.status == "idle"
        assert task.retry_count == 0
        assert task.last_failure_class is None
        assert task.last_run_at == NOW

    # 健康度：三源 healthy，last_success_at 均为本轮时刻
    for code, src in sources.items():
        db_session.refresh(src)
        assert src.health_status == "healthy"
        assert src.last_success_at == NOW

    # 审计：一条汇总，四类失败全 0，任务明细 3 条
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.operation == "采集调度")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit.result == "成功"
    assert sum(audit.detail["failure_classes"].values()) == 0
    assert len(audit.detail["tasks"]) == 3


def test_outside_time_window_nothing_dispatched(db_session):
    """限流时段外不采集（3 源都不动），符合「另配 time_window 限流时段」口径。"""
    sources = _seed_sources(db_session)
    node = db_session.query(Node).filter(Node.code == "node-1").one()
    for code in ("usgs", "gbif", "esa"):
        db_session.add(
            HarvestTask(
                data_source_id=sources[code].id, node_id=node.id,
                schedule_cron="0 * * * *", time_window="09:00-18:00",
                retry_max=3, retry_count=0, status="idle",
                last_run_at=datetime(2026, 12, 28, 8, 0),
            )
        )
    db_session.commit()

    factory = _factory({"usgs": GOOD_PAYLOAD, "gbif": GBIF_PAYLOAD, "esa": ESA_PAYLOAD})
    result = run_dispatch_round(
        db_session, factory, now=datetime(2026, 12, 28, 20, 0)
    )
    assert result.considered == 0
    assert factory.transport.calls == []


def test_paused_task_not_dispatched(db_session):
    sources = _seed_sources(db_session)
    task = _task(db_session, sources["usgs"], status="paused", last_run=NOW - timedelta(hours=1))
    factory = _factory({"usgs": GOOD_PAYLOAD})
    result = run_dispatch_round(db_session, factory, now=NOW, max_items=1)
    assert result.considered == 0
    assert factory.transport.calls == []
