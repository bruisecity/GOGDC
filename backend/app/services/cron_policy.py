"""调度策略解析（W6：cron 表达式 + 限流时段 + 到期判定，纯函数无框架依赖）。

口径来自设计稿第三章字段级设计：
- `schedule_cron`：CRON 表达式，5 段（分 时 日 月 星期），支持 `*`、`*/N`、
  数字、逗号枚举、`a-b` 区间；星期与标准 cron 一致（0/7=周日，1=周一…）。
- `time_window`：限流时段 `HH:MM-HH:MM`，空串=不限；不支持跨零点区间
  （跨零点请写两条窗口或用空值），避免语义含糊。

到期判定按「上一次执行时刻之后该 cron 是否曾到点」实现，而不是比对
「当前分钟是否恰好等于 cron」，因此调度轮询间隔大于 1 分钟也不会漏跑。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

MINUTE = timedelta(minutes=1)


class CronPolicyError(ValueError):
    """调度策略非法（cron / 时段格式错误）。"""


_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _parse_field(text: str, lo: int, hi: int) -> set[int]:
    """解析单个 cron 字段为取值集合（支持 `*` / `*/N` / `a-b` / 枚举）。"""
    text = text.strip()
    if not text:
        raise CronPolicyError("cron 字段为空")
    if text == "*":
        return set(range(lo, hi + 1))

    values: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise CronPolicyError(f"cron 字段含空项: {text!r}")
        if part.startswith("*/"):
            step_text = part[2:]
            if not step_text.isdigit():
                raise CronPolicyError(f"步长非法: {part!r}")
            step = int(step_text)
            if step <= 0:
                raise CronPolicyError(f"步长必须为正: {part!r}")
            values.update(range(lo, hi + 1, step))
            continue
        if "-" in part:
            left, _, right = part.partition("-")
            if not (left.isdigit() and right.isdigit()):
                raise CronPolicyError(f"区间非法: {part!r}")
            a, b = int(left), int(right)
            if a > b:
                raise CronPolicyError(f"区间起点大于终点: {part!r}")
            values.update(range(a, b + 1))
            continue
        if not part.isdigit():
            raise CronPolicyError(f"字段项非数字: {part!r}")
        values.add(int(part))

    for v in values:
        if not (lo <= v <= hi):
            raise CronPolicyError(f"取值 {v} 越界 [{lo},{hi}]: {text!r}")
    return values


def parse_cron(expr: str) -> dict[str, set[int]]:
    """解析 5 段 cron 表达式；空串表示「不按 cron 调度」（由调用方决定语义）。"""
    fields = (expr or "").split()
    if len(fields) != 5:
        raise CronPolicyError(
            f"cron 表达式须为 5 段（分 时 日 月 星期），实际 {len(fields)} 段: {expr!r}"
        )
    out: dict[str, set[int]] = {}
    for name, text, (lo, hi) in zip(
        ("minute", "hour", "day", "month", "weekday"), fields, _FIELD_RANGES
    ):
        out[name] = _parse_field(text, lo, hi)
    # 标准 cron 里 7 与 0 同为周日
    if 7 in out["weekday"]:
        out["weekday"].discard(7)
        out["weekday"].add(0)
    return out


def parse_time_window(text: str) -> Optional[tuple[int, int]]:
    """解析 `HH:MM-HH:MM` 限流时段，返回当日分钟数区间 [start, end)；空串返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    left, sep, right = text.partition("-")
    if not sep:
        raise CronPolicyError(f"限流时段须为 HH:MM-HH:MM: {text!r}")
    try:
        sh, sm = (int(x) for x in left.strip().split(":"))
        eh, em = (int(x) for x in right.strip().split(":"))
    except ValueError as exc:
        raise CronPolicyError(f"限流时段格式非法: {text!r}") from exc
    if not (0 <= sh <= 23 and 0 <= eh <= 24 and 0 <= sm <= 59 and 0 <= em <= 59):
        raise CronPolicyError(f"限流时段越界: {text!r}")
    start, end = sh * 60 + sm, eh * 60 + em
    if start >= end:
        raise CronPolicyError(f"限流时段起点须早于终点（不支持跨零点）: {text!r}")
    return start, end


def in_time_window(text: str, now: datetime) -> bool:
    """当前时刻是否落在限流时段内（空串=不限，恒为 True）。"""
    window = parse_time_window(text)
    if window is None:
        return True
    minute_of_day = now.hour * 60 + now.minute
    return window[0] <= minute_of_day < window[1]


def _cron_minute_matches(fields: dict[str, set[int]], moment: datetime) -> bool:
    # Python weekday(): 周一=0 … 周日=6；cron：周日=0，周一=1 … 与标准对齐
    cron_weekday = (moment.weekday() + 1) % 7
    return (
        moment.minute in fields["minute"]
        and moment.hour in fields["hour"]
        and moment.day in fields["day"]
        and moment.month in fields["month"]
        and cron_weekday in fields["weekday"]
    )


def is_due(
    schedule_cron: str,
    last_run_at: Optional[datetime],
    now: datetime,
    *,
    time_window: str = "",
    lookback_minutes: int = 60,
) -> bool:
    """判断任务是否到期该跑。

    - cron 为空：视为「仅手动触发」，不自动到期。
    - last_run_at 为空（新建任务首轮）：回看 lookback_minutes 内是否已到点，
      到点即视为到期（首轮补跑当期窗口，而不是必须卡在同一分钟创建）。
    - 否则在 (last_run_at, now] 区间内逐分钟扫描 cron 命中，命中且当前
      处于限流时段内即到期；扫描上限 lookback_minutes，防止长时间停机后
      一次性补跑积压轮次（超出说明已错过，下一轮按当前窗口继续）。
    """
    expr = (schedule_cron or "").strip()
    if not expr:
        return False
    fields = parse_cron(expr)
    if not in_time_window(time_window, now):
        return False

    if last_run_at is None:
        start = now - timedelta(minutes=lookback_minutes)
    else:
        start = max(last_run_at, now - timedelta(minutes=lookback_minutes))
    cursor = start.replace(second=0, microsecond=0)
    if last_run_at is not None and cursor <= last_run_at:
        cursor += MINUTE
    while cursor <= now:
        if _cron_minute_matches(fields, cursor):
            return True
        cursor += MINUTE
    return False


def normalized_last_run_at(value: Optional[datetime]) -> Optional[datetime]:
    """统一成 naive UTC：SQLite 存的可能是 naive，PG 是 aware，比较前先对齐。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def utc_now() -> datetime:
    """调度统一时钟（naive UTC，与 updated_at/last_run_at 口径一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
