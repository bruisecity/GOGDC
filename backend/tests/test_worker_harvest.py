"""Worker 装配测试（W6）：多源适配器注入 + beat 调度注册。

不触发真实网络：只验证装配层的选择逻辑与任务注册名，
避免 worker 装配回归（此前 `..config` 层级错误导致模块整体不可导入）。
"""
import pytest


@pytest.fixture()
def worker_mod():
    pytest.importorskip("celery")
    import app.worker.tasks.harvest as mod

    return mod


def test_adapter_for_selects_by_source_code(worker_mod):
    for code in ("usgs", "gbif", "esa"):
        adapter = worker_mod.adapter_for(code)
        assert adapter.code == code


def test_adapter_for_uses_db_base_url(worker_mod):
    adapter = worker_mod.adapter_for("gbif", base_url="https://mirror.example.org")
    # 注入的 base_url 生效（请求地址可验证而无需真出网）
    assert adapter._base_url == "https://mirror.example.org"


def test_adapter_for_rejects_unregistered_source(worker_mod):
    from app.services.usgs import HarvestError

    with pytest.raises(HarvestError) as ei:
        worker_mod.adapter_for("worldpop")
    assert ei.value.failure_class == "元数据校验"
    assert ei.value.retryable is False


def test_task_names_registered(worker_mod):
    assert worker_mod.run_source_harvest.name == "harvest.run_source"
    assert worker_mod.run_usgs_harvest.name == "harvest.run_usgs"   # W2–W5 兼容别名
    assert worker_mod.dispatch_round.name == "harvest.dispatch_round"


def test_beat_schedule_contains_dispatch():
    pytest.importorskip("celery")
    from app.worker.celery_app import celery_app

    assert "harvest-dispatch-5min" in celery_app.conf.beat_schedule
    entry = celery_app.conf.beat_schedule["harvest-dispatch-5min"]
    assert entry["task"] == "harvest.dispatch_round"
