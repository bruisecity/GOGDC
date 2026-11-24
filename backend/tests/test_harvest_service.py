"""采集执行服务测试：一次 run 的状态机闭环（可调度、可重试）。"""
import pytest

from app.models import DataSource, HarvestTask, Node
from app.services.harvest_service import run_harvest_once
from app.services.usgs import USGSTNMClient

from tests.test_usgs_adapter import FakeTransport, GOOD_PAYLOAD


class FailingTransport:
    """固定返回指定失败（HTTP 状态码）。"""

    def __init__(self, status):
        self.status = status

    def request(self, method, url, params):
        return self.status, None


def _make_task(db, *, retry_count=0, retry_max=3):
    ds = db.query(DataSource).filter(DataSource.code == "usgs").one()
    node = db.query(Node).filter(Node.code == "node-1").one()
    task = HarvestTask(
        data_source_id=ds.id,
        node_id=node.id,
        retry_max=retry_max,
        retry_count=retry_count,
        status="idle",
    )
    db.add(task)
    db.commit()
    return task


def test_success_round_trip(db_session):
    task = _make_task(db_session)
    result = run_harvest_once(
        db_session, task.id, USGSTNMClient(FakeTransport(payload=GOOD_PAYLOAD))
    )
    assert result.outcome == "succeeded"
    assert result.items == 1
    db_session.refresh(task)
    assert task.status == "idle"
    assert task.retry_count == 0
    assert task.last_failure_class is None


def test_retryable_failure_schedules_retry(db_session):
    task = _make_task(db_session, retry_count=0, retry_max=3)
    result = run_harvest_once(db_session, task.id, USGSTNMClient(FailingTransport(429)))
    assert result.outcome == "retry_scheduled"
    db_session.refresh(task)
    assert task.status == "idle"          # 回 idle 等下一轮调度
    assert task.retry_count == 1
    assert task.last_failure_class == "源站限流"


def test_retry_exhausted_marks_failed(db_session):
    task = _make_task(db_session, retry_count=3, retry_max=3)
    result = run_harvest_once(db_session, task.id, USGSTNMClient(FailingTransport(429)))
    assert result.outcome == "failed"
    db_session.refresh(task)
    assert task.status == "failed"
    assert task.retry_count == 3
    assert task.last_failure_class == "源站限流"


def test_non_retryable_failure_marks_failed_immediately(db_session):
    task = _make_task(db_session, retry_count=0, retry_max=3)
    result = run_harvest_once(db_session, task.id, USGSTNMClient(FailingTransport(403)))
    assert result.outcome == "failed"
    db_session.refresh(task)
    assert task.status == "failed"
    assert task.retry_count == 0        # 凭证失效不消耗重试次数
    assert task.last_failure_class == "凭证失效"


def test_failed_task_cannot_restart_directly(db_session):
    from app.domain.states import InvalidTransition

    task = _make_task(db_session)
    task.status = "failed"
    db_session.commit()
    with pytest.raises(InvalidTransition):
        run_harvest_once(
            db_session, task.id, USGSTNMClient(FakeTransport(payload=GOOD_PAYLOAD))
        )


def test_success_resets_retry_counter(db_session):
    task = _make_task(db_session, retry_count=2, retry_max=3)
    result = run_harvest_once(
        db_session, task.id, USGSTNMClient(FakeTransport(payload=GOOD_PAYLOAD))
    )
    assert result.outcome == "succeeded"
    db_session.refresh(task)
    assert task.retry_count == 0
