"""USGS 适配器测试：假 transport 离线覆盖成功与四类失败分类。"""
import pytest

from app.services.usgs import USGSTNMClient


class FakeTransport:
    def __init__(self, status=200, payload=None, exc=None):
        self.status, self.payload, self.exc = status, payload, exc
        self.calls = []

    def request(self, method, url, params):
        self.calls.append((method, url, params))
        if self.exc is not None:
            raise self.exc
        return self.status, self.payload


GOOD_PAYLOAD = {
    "products": [
        {
            "title": "NED 1/3 arc-second tile",
            "downloadURL": "https://example.com/tile.tif",
            "sizeInBytes": 12345,
            "format": "GeoTIFF",
            "boundingBox": [-105.0, 39.0, -104.0, 40.0],
        }
    ]
}


def _client(transport):
    return USGSTNMClient(transport)


def test_fetch_products_success():
    t = FakeTransport(payload=GOOD_PAYLOAD)
    items = _client(t).fetch_products()
    assert len(items) == 1
    assert items[0].url == "https://example.com/tile.tif"
    assert items[0].size_bytes == 12345
    # 请求形参符合 TNM 契约
    method, url, params = t.calls[0]
    assert method == "GET"
    assert url.endswith("/tnmaccess/api/products")
    assert params["outputFormat"] == "JSON"


@pytest.mark.parametrize(
    "status,expected_class,expected_retryable",
    [
        (429, "源站限流", True),
        (503, "源站限流", True),
        (403, "凭证失效", False),
        (401, "凭证失效", False),
        (500, "连接超时", True),
    ],
)
def test_http_failure_classification(status, expected_class, expected_retryable):
    from app.services.usgs import HarvestError

    client = _client(FakeTransport(status=status, payload=None))
    with pytest.raises(HarvestError) as ei:
        client.fetch_products()
    assert ei.value.failure_class == expected_class
    assert ei.value.retryable is expected_retryable


def test_network_exception_maps_to_timeout():
    from app.services.usgs import HarvestError

    client = _client(FakeTransport(exc=TimeoutError("connect timeout")))
    with pytest.raises(HarvestError) as ei:
        client.fetch_products()
    assert ei.value.failure_class == "连接超时"
    assert ei.value.retryable is True


def test_bad_shape_maps_to_metadata_validation():
    from app.services.usgs import HarvestError

    client = _client(FakeTransport(payload={"unexpected": []}))
    with pytest.raises(HarvestError) as ei:
        client.fetch_products()
    assert ei.value.failure_class == "元数据校验"
    assert ei.value.retryable is False


def test_product_without_url_rejected():
    from app.services.usgs import HarvestError

    payload = {"products": [{"title": "no url"}]}
    client = _client(FakeTransport(payload=payload))
    with pytest.raises(HarvestError) as ei:
        client.fetch_products()
    assert ei.value.failure_class == "元数据校验"
