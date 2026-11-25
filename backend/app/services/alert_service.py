"""存储告警 webhook 送达（W5 补齐：送达 + 重试机制）。

口径：
- 告警/恢复事件 POST JSON 到 ALERT_WEBHOOK_URLS（逗号分隔多路，逐路独立送达）；
- 每路独立重试：固定上限次数（ALERT_WEBHOOK_RETRIES，默认 3 次），指数退避
  0.5s/1s/1.5s…；2xx 视为送达成功；
- 送达结果回写告警审计 detail.webhook（url/result/attempts/error），失败不影响
  告警审计本身落库——webhook 不可用时审计日志仍是可达兜底通道；
- 传输层可注入（与 S3CompatBlobStore 同口径）：测试离线可跑，生产默认 httpx。
"""
from __future__ import annotations

import datetime as dt
import json
import time


class HTTPXTransport:
    """生产默认传输：httpx 同步 POST。"""

    def request(self, method: str, url: str, headers: dict, body: bytes):
        import httpx

        resp = httpx.request(
            method, url, headers=headers, content=body, timeout=10.0
        )
        return resp.status_code, resp.content


class WebhookDispatcher:
    """告警事件多路 webhook 送达器（逐路重试，失败不互相影响）。"""

    def __init__(
        self,
        urls: list[str],
        *,
        transport=None,
        max_attempts: int = 3,
        base_backoff: float = 0.5,
        sleep=time.sleep,
    ) -> None:
        if not urls:
            raise ValueError("webhook urls 不能为空")
        self._urls = list(urls)
        self._transport = transport or HTTPXTransport()
        self._max_attempts = max(1, int(max_attempts))
        self._base_backoff = base_backoff
        self._sleep = sleep

    @staticmethod
    def build_event(result: str, status: dict, alert_ratio: float) -> dict:
        """告警审计 detail → webhook 载荷（含事件类型与告警线）。"""
        return {
            "event": result,  # 告警 / 恢复
            "source": "gogdc-storage-watermark",
            "alert_ratio": alert_ratio,
            "occurred_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
            **{k: status[k] for k in
               ("bucket", "tier", "used_bytes", "quota_bytes", "ratio", "status")},
        }

    def dispatch(self, event: dict) -> list[dict]:
        """逐路送达；返回各路结果（成功/失败 + 尝试次数）。"""
        payload = json.dumps(event, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json"}
        outcomes: list[dict] = []
        for url in self._urls:
            attempts = 0
            ok = False
            last_error: str | None = None
            for i in range(self._max_attempts):
                attempts += 1
                try:
                    status, _body = self._transport.request(
                        "POST", url, headers, payload
                    )
                    if 200 <= status < 300:
                        ok = True
                        last_error = None
                        break
                    last_error = f"HTTP {status}"
                except Exception as exc:  # 网络/超时等异常同样进入重试
                    last_error = type(exc).__name__
                if i < self._max_attempts - 1:
                    self._sleep(self._base_backoff * (i + 1))  # 退避：0.5s, 1s, ...
            outcome: dict = {"url": url, "result": "成功" if ok else "失败",
                             "attempts": attempts}
            if last_error is not None:
                outcome["error"] = last_error
            outcomes.append(outcome)
        return outcomes
