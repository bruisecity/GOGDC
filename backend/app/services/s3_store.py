"""S3 兼容对象存储实现（W5 存储分级切片：Ceph RGW / MinIO 等）。

设计要点（技术方案第四章）：
- storage_key 始终存「相对键」，本实现按 `{endpoint}/{bucket}/{key}` 寻址；
- SigV4 签名用标准库 hashlib/hmac 实现，不引入 boto3（CI 依赖最小化）；
- 传输层可注入（`transport.request(method, url, headers, body) ->
  (status, headers, body)`），与 USGSTNMClient 同口径：测试离线可跑，
  生产默认走 httpx。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import urllib.parse

from .storage import BlobStoreError, StoredBlob

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


class HTTPXTransport:
    """生产默认传输：httpx 同步请求。"""

    def request(self, method: str, url: str, headers: dict, body: bytes):
        import httpx

        resp = httpx.request(method, url, headers=headers, content=body, timeout=60.0)
        return resp.status_code, dict(resp.headers), resp.content


class S3CompatBlobStore:
    """S3 兼容 BlobStore（path-style 寻址 + AWS SigV4）。

    仅实现 BlobStore 最小协议：put / get / exists / delete。
    """

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        *,
        region: str = "us-east-1",
        transport=None,
        clock=None,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise BlobStoreError(f"S3 endpoint 必须为 http(s) URL: {endpoint!r}")
        if not bucket or "/" in bucket:
            raise BlobStoreError(f"bucket 名非法: {bucket!r}")
        self._endpoint = endpoint.rstrip("/")
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._transport = transport or HTTPXTransport()
        # clock 返回 UTC datetime：签名时间可注入，测试确定性
        self._clock = clock or dt.datetime.now(tz=dt.timezone.utc)

    # ---- SigV4 签名 ----

    def _amz_date(self) -> tuple[str, str]:
        now = self._clock() if callable(self._clock) else self._clock
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        return stamp, now.strftime("%Y%m%d")

    def _authorization(
        self, method: str, canonical_uri: str, payload_sha256: str, amz_stamp: str, amz_date: str, host: str
    ) -> str:
        canonical_headers = (
            f"host:{host}\nx-amz-content-sha256:{payload_sha256}\nx-amz-date:{amz_stamp}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [method, canonical_uri, "", canonical_headers, signed_headers, payload_sha256]
        )
        scope = f"{amz_date}/{self._region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_stamp,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )
        key = _sign(
            _sign(
                _sign(_sign((f"AWS4{self._secret_key}").encode(), amz_date), self._region),
                "s3",
            ),
            "aws4_request",
        )
        signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        return (
            f"AWS4-HMAC-SHA256 Credential={self._access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

    def _call(self, method: str, key: str, payload: bytes = b"") -> tuple[int, bytes]:
        if not key or key.startswith("/") or ".." in urllib.parse.urlparse(key).path.split("/"):
            raise BlobStoreError(f"存储键必须为相对键: {key!r}")
        quoted = "/".join(urllib.parse.quote(seg, safe="") for seg in key.split("/"))
        canonical_uri = f"/{self._bucket}/{quoted}"
        host = urllib.parse.urlparse(self._endpoint).netloc
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        amz_stamp, amz_date = self._amz_date()
        headers = {
            "Host": host,
            "x-amz-content-sha256": payload_sha256,
            "x-amz-date": amz_stamp,
            "Authorization": self._authorization(
                method, canonical_uri, payload_sha256, amz_stamp, amz_date, host
            ),
        }
        status, _resp_headers, body = self._transport.request(
            method, f"{self._endpoint}{canonical_uri}", headers, payload
        )
        if status >= 400:
            raise BlobStoreError(f"S3 {method} 失败 status={status} key={key!r}")
        return status, body

    # ---- BlobStore 协议 ----

    def put(self, key: str, payload: bytes) -> StoredBlob:
        self._call("PUT", key, payload)
        return StoredBlob(size_bytes=len(payload), checksum=hashlib.sha256(payload).hexdigest())

    def get(self, key: str) -> bytes:
        _status, body = self._call("GET", key)
        return body

    def exists(self, key: str) -> bool:
        try:
            self._call("HEAD", key)
            return True
        except BlobStoreError:
            return False

    def delete(self, key: str) -> None:
        self._call("DELETE", key)
