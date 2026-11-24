"""状态机单元测试：采集任务 / 处置五步 / 发布五步。"""
import pytest

from app.domain.states import (
    DISPOSAL_STEPS,
    HARVEST_TRANSITIONS,
    PUBLISH_STEPS,
    InvalidTransition,
    disposal_transition,
    harvest_transition,
    publish_transition,
)


class TestHarvest:
    def test_legal_transitions(self):
        assert harvest_transition("idle", "running") == "running"
        assert harvest_transition("running", "failed") == "failed"
        assert harvest_transition("failed", "idle") == "idle"      # 重试回到排队
        assert harvest_transition("paused", "idle") == "idle"      # 恢复
        assert harvest_transition("idle", "paused") == "paused"    # 暂停

    def test_all_states_covered(self):
        assert set(HARVEST_TRANSITIONS) == {"idle", "running", "failed", "paused"}

    def test_illegal(self):
        with pytest.raises(InvalidTransition):
            harvest_transition("idle", "failed")    # 未运行不可能失败
        with pytest.raises(InvalidTransition):
            harvest_transition("paused", "running")  # 恢复必须经 idle 重新调度
        with pytest.raises(InvalidTransition):
            harvest_transition("bogus", "idle")


class TestDisposal:
    def test_forward_only(self):
        for cur, nxt in zip(DISPOSAL_STEPS, DISPOSAL_STEPS[1:]):
            assert disposal_transition(cur, nxt) == nxt

    def test_stay_allowed(self):
        assert disposal_transition("人工复核", "人工复核") == "人工复核"

    def test_no_skip_no_rollback(self):
        with pytest.raises(InvalidTransition):
            disposal_transition("待处置", "执行处置")   # 跳过人工复核
        with pytest.raises(InvalidTransition):
            disposal_transition("二次确认", "人工复核")  # 回退
        with pytest.raises(InvalidTransition):
            disposal_transition("待处置", "不存在")


class TestPublish:
    def test_forward_only(self):
        for cur, nxt in zip(PUBLISH_STEPS, PUBLISH_STEPS[1:]):
            assert publish_transition(cur, nxt) == nxt

    def test_no_rollback(self):
        with pytest.raises(InvalidTransition):
            publish_transition("预览", "草稿")
        with pytest.raises(InvalidTransition):
            publish_transition("草稿", "发布")       # 必须经字段校验与预览
