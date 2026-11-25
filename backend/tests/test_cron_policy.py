"""调度策略解析与到期判定测试（W6）。"""
from datetime import datetime, timedelta

import pytest

from app.services.cron_policy import (
    CronPolicyError,
    in_time_window,
    is_due,
    parse_cron,
    parse_time_window,
    utc_now,
)


def test_parse_cron_fields():
    f = parse_cron("*/15 9-18 1,15 * 1-5")
    assert f["minute"] == {0, 15, 30, 45}
    assert f["hour"] == set(range(9, 19))
    assert f["day"] == {1, 15}
    assert f["month"] == set(range(1, 13))
    assert f["weekday"] == {1, 2, 3, 4, 5}


def test_parse_cron_weekday_7_normalized_to_0():
    f = parse_cron("0 0 * * 7")
    assert f["weekday"] == {0}


@pytest.mark.parametrize("expr", ["", "0 9 * *", "0 9 * * * *", "60 9 * * *", "0 24 * * *", "*/0 * * * *"])
def test_parse_cron_rejects_invalid(expr):
    with pytest.raises(CronPolicyError):
        parse_cron(expr)


def test_parse_time_window():
    assert parse_time_window("09:00-18:00") == (540, 1080)
    assert parse_time_window("") is None
    assert parse_time_window("   ") is None


@pytest.mark.parametrize("text", ["09:00", "09:00-09:00", "18:00-09:00", "25:00-26:00", "aa:bb-cc:dd"])
def test_parse_time_window_rejects_invalid(text):
    with pytest.raises(CronPolicyError):
        parse_time_window(text)


def test_in_time_window_bounds():
    assert in_time_window("", datetime(2026, 12, 28, 3, 0)) is True
    assert in_time_window("09:00-18:00", datetime(2026, 12, 28, 9, 0)) is True
    assert in_time_window("09:00-18:00", datetime(2026, 12, 28, 17, 59)) is True
    assert in_time_window("09:00-18:00", datetime(2026, 12, 28, 18, 0)) is False
    assert in_time_window("09:00-18:00", datetime(2026, 12, 28, 8, 59)) is False


def test_is_due_empty_cron_is_manual_only():
    assert is_due("", None, datetime(2026, 12, 28, 9, 0)) is False


def test_is_due_first_round_catches_up_within_lookback():
    now = datetime(2026, 12, 28, 9, 15)
    assert is_due("0 9 * * *", None, now) is True            # 15 分钟内已到点
    assert is_due("0 9 * * *", None, datetime(2026, 12, 28, 14, 0)) is False


def test_is_due_respects_last_run():
    ran = datetime(2026, 12, 28, 9, 0)
    assert is_due("0 9 * * *", ran, datetime(2026, 12, 28, 9, 5)) is False
    assert is_due("0 9 * * *", ran, datetime(2026, 12, 29, 9, 5)) is True


def test_is_due_hourly_catches_single_slot_after_gap():
    # 3 小时前跑过，每小时一次：应有到点命中（扫描而不是精确比对当前分钟）
    last = datetime(2026, 12, 28, 6, 30)
    assert is_due("0 * * * *", last, datetime(2026, 12, 28, 9, 5)) is True


def test_is_due_lookback_caps_backlog():
    # 停机 8 小时，lookback 60 分钟：仍到期（最近一小时内有点），但只算一次轮次
    last = datetime(2026, 12, 28, 1, 0)
    assert is_due("0 * * * *", last, datetime(2026, 12, 28, 9, 5)) is True
    # 当前分钟不在任何 cron 点且最近一小时内无点时不到期
    assert is_due("30 2 * * *", last, datetime(2026, 12, 28, 9, 5)) is False


def test_is_due_blocked_outside_time_window():
    last = datetime(2026, 12, 28, 7, 0)
    assert is_due("0 * * * *", last, datetime(2026, 12, 28, 20, 0),
                  time_window="09:00-18:00") is False
    assert is_due("0 * * * *", last, datetime(2026, 12, 28, 10, 0),
                  time_window="09:00-18:00") is True


def test_is_due_weekday_only_runs_on_weekdays():
    # 2026-12-28 是周一；2026-12-27 是周日
    assert is_due("30 8 * * 1-5", None, datetime(2026, 12, 28, 9, 0)) is True
    assert is_due("30 8 * * 1-5", None, datetime(2026, 12, 27, 9, 0)) is False


def test_utc_now_is_naive():
    assert utc_now().tzinfo is None
