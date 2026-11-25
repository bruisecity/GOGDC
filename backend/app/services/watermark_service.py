"""容量水位监控与告警（W5 切片 C）。

口径（技术方案第四章）：
- 水位 = used_bytes / quota_bytes，告警线 80%（quota_bytes 列注释即 80% 告警线）；
- 告警「可达」：每条告警/恢复都落 AuditLog（operation=容量水位），
  可经审计控制台 API 按 operation 过滤检索，可配 IM/webhook 通知（预案文档）；
- 去重：仍处告警态不重复告警；水位回落后落一条「恢复」，再次越线才再告警。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditLog, StorageBucket
from .alert_service import WebhookDispatcher

WATERMARK_OPERATION = "容量水位"
ALERT_RATIO = 0.8


def bucket_status(bucket: StorageBucket) -> dict:
    """单桶水位快照（只读计算，不落库）。"""
    used = bucket.used_bytes or 0
    quota = bucket.quota_bytes or 0
    if quota <= 0:
        return {
            "bucket": bucket.name, "tier": bucket.tier,
            "used_bytes": used, "quota_bytes": quota,
            "ratio": None, "status": "未设配额",
        }
    ratio = round(used / quota, 4)
    return {
        "bucket": bucket.name, "tier": bucket.tier,
        "used_bytes": used, "quota_bytes": quota,
        "ratio": ratio, "status": "告警" if ratio >= ALERT_RATIO else "正常",
    }


def all_bucket_statuses(db: Session) -> list[dict]:
    return [
        bucket_status(b)
        for b in db.query(StorageBucket).order_by(StorageBucket.id).all()
    ]


def _last_state(db: Session, bucket_name: str) -> str | None:
    """该桶最近一条容量水位记录的结果（告警/恢复）；无记录返回 None。"""
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.operation == WATERMARK_OPERATION)
        .order_by(AuditLog.id.desc())
        .limit(200)
    )
    for row in rows:
        if (row.detail or {}).get("bucket") == bucket_name:
            return row.result
    return None


def check_watermarks(
    db: Session,
    *,
    user_id: int | None = None,
    webhook_urls: list[str] | None = None,
    dispatcher: WebhookDispatcher | None = None,
) -> list[dict]:
    """巡检全部桶的水位：越线落「告警」审计，回落落「恢复」审计，常态静默。

    状态变化时同步走 webhook 送达（ALERT_WEBHOOK_URLS 多路 + 逐路重试），
    送达结果回写该条审计的 detail.webhook；webhook 全失败不影响审计落库
    （审计日志始终是可达兜底通道）。webhook_urls=[] 显式禁用推送。

    返回当前仍处告警态的桶列表（水位快照）。巡检幂等：未发生状态变化不落审计。
    """
    if webhook_urls is None:
        from ..config import settings
        webhook_urls = list(settings.alert_webhook_urls)

    alerts: list[dict] = []
    changed = False
    rows: list = []  # (审计行, 状态快照, 事件结果)
    for bucket in db.query(StorageBucket).order_by(StorageBucket.id).all():
        status = bucket_status(bucket)
        if status["ratio"] is None:
            continue
        if status["status"] == "告警":
            alerts.append(status)
            if _last_state(db, bucket.name) != "告警":
                row = AuditLog(
                    user_id=user_id, operation=WATERMARK_OPERATION,
                    resource="storage", result="告警", detail=dict(status),
                )
                db.add(row)
                rows.append((row, status, "告警"))
                changed = True
        else:
            if _last_state(db, bucket.name) == "告警":
                row = AuditLog(
                    user_id=user_id, operation=WATERMARK_OPERATION,
                    resource="storage", result="恢复", detail=dict(status),
                )
                db.add(row)
                rows.append((row, status, "恢复"))
                changed = True
    if changed:
        if webhook_urls and rows:
            disp = dispatcher or WebhookDispatcher(
                webhook_urls,
                max_attempts=_alert_retries(),
            )
            for row, status, result in rows:
                event = WebhookDispatcher.build_event(result, status, ALERT_RATIO)
                row.detail = {**(row.detail or {}), "webhook": disp.dispatch(event)}
        db.commit()
    return alerts


def _alert_retries() -> int:
    from ..config import settings
    return settings.alert_webhook_retries
