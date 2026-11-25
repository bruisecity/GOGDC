"""S3 兼容 BlobStore 测试（W5 切片 A：SigV4 签名 + 协议行为，离线可跑）。"""
from __future__ import annotations

import datetime as dt
import hashlib

import pytest

from app.services.s3_store import S3CompatBlobStore
from app.services.storage import BlobStoreError

CLOCK = dt.datetime(2026, 12, 21, 2, 30, 0, tzinfo=dt.timezone.utc)


class FakeTransport:
    """记录请求并按 (method, key) 返回预置响应。"""

    def __init__(self):
        self.calls = []
        self.responses = {}  # (method, key) -> (status, body)

    def respond(self, method, key, status, body=b""):
        self.responses[(method, key)] = (status, body)

    def request(self, method, url, headers, body):
        key = url.rsplit("/", 1)[-1] if "/" in url else url
        # url = endpoint/bucket/key1/.../keyN：取 bucket 之后的整段
        path = url.split("/geodata-hot/", 1)[-1] if "/geodata-hot/" in url else key
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        status, resp_body = self.responses.get((method, path), (200, b""))
        return status, {}, resp_body


def make_store(transport=None):
    return S3CompatBlobStore(
        "http://rgw.example", "geodata-hot", "AKIDEXAMPLE", "secret",
        region="us-east-1", transport=transport or FakeTransport(), clock=CLOCK,
    )


def test_put_signs_with_sigv4_and_sha256():
    t = FakeTransport()
    store = make_store(t)
    payload = b"dem-bytes"
    blob = store.put("usgs/123/dem.tif", payload)
    assert blob.size_bytes == len(payload)
    assert blob.checksum == hashlib.sha256(payload).hexdigest()

    call = t.calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == "http://rgw.example/geodata-hot/usgs/123/dem.tif"
    assert call["body"] == payload
    h = call["headers"]
    assert h["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert h["x-amz-date"] == "20261221T023000Z"
    assert h["x-amz-content-sha256"] == hashlib.sha256(payload).hexdigest()


def test_signature_deterministic():
    t1, t2 = FakeTransport(), FakeTransport()
    make_store(t1).put("a/b.bin", b"x")
    make_store(t2).put("a/b.bin", b"x")
    assert t1.calls[0]["headers"]["Authorization"] == t2.calls[0]["headers"]["Authorization"]


def test_get_returns_body_and_404_raises():
    t = FakeTransport()
    t.respond("GET", "usgs/1/a.tif", 200, b"content")
    t.respond("GET", "missing", 404, b" NoSuchKey")
    store = make_store(t)
    assert store.get("usgs/1/a.tif") == b"content"
    with pytest.raises(BlobStoreError):
        store.get("missing")


def test_exists_maps_head_status():
    t = FakeTransport()
    t.respond("HEAD", "gone", 404)
    store = make_store(t)
    assert store.exists("present") is True  # 默认 200
    assert store.exists("gone") is False


def test_delete_uses_delete_method():
    t = FakeTransport()
    make_store(t).delete("usgs/1/a.tif")
    assert t.calls[0]["method"] == "DELETE"


def test_invalid_config_and_keys_rejected():
    with pytest.raises(BlobStoreError):
        S3CompatBlobStore("rgw.example", "b", "ak", "sk")
    with pytest.raises(BlobStoreError):
        S3CompatBlobStore("http://rgw", "bad/bucket", "ak", "sk")
    t = FakeTransport()
    store = make_store(t)
    with pytest.raises(BlobStoreError):
        store.put("/abs/path", b"x")
    with pytest.raises(BlobStoreError):
        store.put("../escape", b"x")
    assert t.calls == []  # 键非法在签名前拦截


def test_bucket_management():
    t = FakeTransport()
    store = make_store(t)
    # 建桶：PUT /geodata-hot/
    store.create_bucket()
    assert t.calls[0]["method"] == "PUT"
    assert t.calls[0]["url"] == "http://rgw.example/geodata-hot/"
    assert t.calls[0]["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256")
    # 已存在（409）幂等不报错
    t2 = FakeTransport(); t2.respond("PUT", "", 409)
    make_store(t2).create_bucket()
    # 其他错误仍报错
    t3 = FakeTransport(); t3.respond("PUT", "", 403)
    with pytest.raises(BlobStoreError):
        make_store(t3).create_bucket()
    # 桶存在性
    assert store.bucket_exists() is True  # 默认 200
    t4 = FakeTransport(); t4.respond("HEAD", "", 404)
    assert make_store(t4).bucket_exists() is False


def test_key_segments_url_quoted():
    t = FakeTransport()
    make_store(t).put("空间 名/文件 1.tif", b"x")
    assert t.calls[0]["url"].endswith(
        "/geodata-hot/" + "%E7%A9%BA%E9%97%B4%20%E5%90%8D/%E6%96%87%E4%BB%B6%201.tif"
    )
