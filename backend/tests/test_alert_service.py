"""告警 webhook 送达与重试测试（W5 补齐：送达 + 重试机制，离线可跑）。"""
from __future__ import annotations

import json

import pytest

from app.services.alert_service import WebhookDispatcher

STATUS = {"bucket": "geodata-hot", "tier": "\u70ed", "used_bytes": 900,
          "quota_bytes": 1000, "ratio": 0.9, "status": "\u544a\u8b66"}


class FakeTransport:
    """按调用序返回预置 (status, body) 或抛异常。"""

    def __init__(self, script):
        self.script = list(script)  # 每元素: int=status | Exception
        self.calls = []

    def request(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url,
                           "headers": headers, "body": body})
        step = self.script.pop(0) if self.script else (200, b"")
        if isinstance(step, Exception):
            raise step
        return step, b""


def make_dispatcher(script, urls=("http://hook.example/a",), **kw):
    t = FakeTransport(script)
    sleeps = []
    d = WebhookDispatcher(list(urls), transport=t,
                          sleep=sleeps.append, **kw)
    return d, t, sleeps


def test_first_try_success_no_backoff():
    d, t, sleeps = make_dispatcher([200])
    out = d.dispatch(WebhookDispatcher.build_event("\u544a\u8b66", STATUS, 0.8))
    assert out == [{"url": "http://hook.example/a", "result": "\u6210\u529f", "attempts": 1}]
    assert sleeps == []  # 一次成功不退避
    call = t.calls[0]
    assert call["method"] == "POST"
    assert call["headers"]["Content-Type"] == "application/json"
    payload = json.loads(call["body"])
    assert payload["event"] == "\u544a\u8b66"
    assert payload["source"] == "gogdc-storage-watermark"
    assert payload["alert_ratio"] == 0.8
    assert payload["bucket"] == "geodata-hot" and payload["ratio"] == 0.9
    assert "occurred_at" in payload


def test_retry_then_success_records_attempts():
    d, t, sleeps = make_dispatcher([503, ConnectionError("down"), 200])
    out = d.dispatch(WebhookDispatcher.build_event("\u544a\u8b66", STATUS, 0.8))
    assert out[0]["result"] == "\u6210\u529f"
    assert out[0]["attempts"] == 3
    assert "error" not in out[0]
    assert sleeps == [0.5, 1.0]  # 指数退避


def test_all_attempts_fail_marks_failure_with_error():
    d, t, sleeps = make_dispatcher([500, 500, 500], max_attempts=3)
    out = d.dispatch(WebhookDispatcher.build_event("\u544a\u8b66", STATUS, 0.8))
    assert out[0]["result"] == "\u5931\u8d25"
    assert out[0]["attempts"] == 3
    assert out[0]["error"] == "HTTP 500"
    assert len(t.calls) == 3
    assert sleeps == [0.5, 1.0]


def test_non_2xx_and_exception_both_retry():
    d, t, sleeps = make_dispatcher([TimeoutError("t"), 404, 204])
    out = d.dispatch(WebhookDispatcher.build_event("\u6062\u590d", STATUS, 0.8))
    assert out[0]["result"] == "\u6210\u529f" and out[0]["attempts"] == 3


def test_multiple_urls_independent():
    d, t, sleeps = make_dispatcher(
        [500, 500, 500, 200],
        urls=("http://hook.example/a", "http://hook.example/b"),
    )
    out = d.dispatch(WebhookDispatcher.build_event("\u544a\u8b66", STATUS, 0.8))
    assert [o["result"] for o in out] == ["\u5931\u8d25", "\u6210\u529f"]
    assert {o["url"] for o in out} == {"http://hook.example/a", "http://hook.example/b"}
    assert out[0]["attempts"] == 3 and out[1]["attempts"] == 1


def test_empty_urls_rejected():
    with pytest.raises(ValueError):
        WebhookDispatcher([])


def test_recovery_event_payload():
    d, t, _ = make_dispatcher([200])
    ok_status = dict(STATUS, status="\u6b63\u5e38", ratio=0.5, used_bytes=500)
    d.dispatch(WebhookDispatcher.build_event("\u6062\u590d", ok_status, 0.8))
    payload = json.loads(t.calls[0]["body"])
    assert payload["event"] == "\u6062\u590d"
    assert payload["status"] == "\u6b63\u5e38" and payload["ratio"] == 0.5
