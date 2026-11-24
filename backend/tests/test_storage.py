"""本地存储实现测试（W3）。"""
import pytest

from app.services.storage import BlobStoreError, LocalBlobStore


def test_put_get_roundtrip(tmp_path):
    store = LocalBlobStore(tmp_path / "s")
    blob = store.put("usgs/1/a.tif", b"hello-geotiff")
    assert blob.size_bytes == len(b"hello-geotiff")
    assert len(blob.checksum) == 64  # sha256 hex
    assert store.get("usgs/1/a.tif") == b"hello-geotiff"
    assert store.exists("usgs/1/a.tif")
    assert not store.exists("usgs/1/missing.bin")


def test_nested_key_creates_parents(tmp_path):
    store = LocalBlobStore(tmp_path)
    store.put("a/b/c/d.bin", b"x")
    assert store.get("a/b/c/d.bin") == b"x"


def test_rejects_path_escape(tmp_path):
    store = LocalBlobStore(tmp_path)
    with pytest.raises(BlobStoreError):
        store.put("../escape.bin", b"x")
    with pytest.raises(BlobStoreError):
        store.put("/absolute.bin", b"x")


def test_get_missing_raises(tmp_path):
    store = LocalBlobStore(tmp_path)
    with pytest.raises(BlobStoreError):
        store.get("nope.bin")
