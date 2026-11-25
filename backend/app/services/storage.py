"""存储抽象与本地实现（W3 采集落盘切片）。

设计要点（技术方案第四章）：
- storage_key 始终存「相对键」，与存储路线（object=S3 兼容 / file=挂载）解耦；
- BlobStore 只约定 put/get 两个最小能力，生产替换为 S3/Ceph RGW 客户端；
- 本地实现 LocalBlobStore 面向开发与汇聚节点 file 路线，含路径逃逸防护。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class StoredBlob:
    """一次 put 的落盘结果：大小 + sha256 校验和。"""

    size_bytes: int
    checksum: str  # sha256 hex


class BlobStore(Protocol):
    """对象存储最小协议（W5 增加 delete：分层迁移需删除源端对象）。"""

    def put(self, key: str, payload: bytes) -> StoredBlob: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


class BlobStoreError(Exception):
    """存储操作失败（键非法 / 对象缺失等）。"""


class LocalBlobStore:
    """本地文件系统实现：base_dir 下的相对键落盘。

    键规则：相对路径；拒绝绝对路径与 `..` 逃逸。
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir).resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        if not key or key.startswith("/") or key.startswith("\\"):
            raise BlobStoreError(f"存储键必须为相对键: {key!r}")
        path = (self._base / key).resolve()
        if self._base not in path.parents and path != self._base:
            raise BlobStoreError(f"存储键越界: {key!r}")
        if ".." in Path(key).parts:
            raise BlobStoreError(f"存储键含 '..': {key!r}")
        return path

    def put(self, key: str, payload: bytes) -> StoredBlob:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return StoredBlob(size_bytes=len(payload), checksum=hashlib.sha256(payload).hexdigest())

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.is_file():
            raise BlobStoreError(f"对象不存在: {key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self._resolve(key).is_file()
        except BlobStoreError:
            return False

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        if not path.is_file():
            raise BlobStoreError(f"对象不存在: {key}")
        path.unlink()
