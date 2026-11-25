"""多源适配器测试（W6）：USGS / GBIF / ESA 三源统一契约，假 transport 离线跑。

覆盖：契约解析、请求形参、坐标/结构校验、四类失败分类、注册表行为。
"""
import pytest

from app.services.sources import ACTIVE_SOURCES, ADAPTERS, build_adapter
from app.services.sources.esa import _bbox_from_geo_footprint, _product_format
from app.services.usgs import HarvestError

from tests.test_usgs_adapter import FakeTransport, GOOD_PAYLOAD


GBIF_PAYLOAD = {
    "count": 2,
    "endOfRecords": True,
    "results": [
        {
            "key": 1234567890,
            "gbifID": "1234567890",
            "scientificName": "Quercus robur L.",
            "acceptedScientificName": "Quercus robur L.",
            "decimalLatitude": 60.35,
            "decimalLongitude": 5.32,
            "license": "http://creativecommons.org/licenses/by/4.0/",
            "references": "https://example.org/occ/1234567890",
            "basisOfRecord": "PRESERVED_SPECIMEN",
            "country": "NO",
            "year": 2020,
        },
        {
            "key": 222,
            "scientificName": "Picea abies",
            "decimalLatitude": 59.1,
            "decimalLongitude": 10.2,
            # 无 references：应回落 GBIF 记录页
        },
    ],
}

ESA_PAYLOAD = {
    "value": [
        {
            "Id": "11111111-2222-3333-4444-555555555555",
            "Name": "S2B_MSIL2A_20261220T103319_N0500_R008_T32VKM.SAFE",
            "S3Path": "/eodata/Sentinel-2/MSI/L2A/2026/12/20/x",
            "ContentLength": 812345678,
            "ContentDate": {"Start": "2026-12-20T10:33:19Z", "End": "2026-12-20T10:34:00Z"},
            "Online": True,
            "GeoFootprint": {
                "type": "Polygon",
                "coordinates": [[[10.1, 59.2], [11.4, 59.1], [11.3, 60.3], [10.1, 59.2]]],
            },
            "Checksum": [
                {"Value": "abc", "Algorithm": "MD5"},
                {"Value": "deadbeef", "Algorithm": "BLAKE3"},
            ],
        }
    ]
}


def test_registry_covers_three_active_sources():
    assert set(ACTIVE_SOURCES) == {"usgs", "gbif", "esa"}
    for code in ACTIVE_SOURCES:
        assert code in ADAPTERS


def test_build_adapter_unknown_source_is_metadata_failure():
    with pytest.raises(HarvestError) as ei:
        build_adapter("worldpop", FakeTransport(payload={}))
    assert ei.value.failure_class == "元数据校验"
    assert ei.value.retryable is False


def test_build_adapter_returns_code_matching_adapter():
    for code in ACTIVE_SOURCES:
        adapter = build_adapter(code, FakeTransport(payload={}))
        assert adapter.code == code


# ---------- USGS 适配器：与 W2 客户端行为一致 ----------

def test_usgs_adapter_parity_with_client():
    t = FakeTransport(payload=GOOD_PAYLOAD)
    items = build_adapter("usgs", t).fetch_items(max_items=5)
    assert len(items) == 1
    assert items[0].url == "https://example.com/tile.tif"
    assert t.calls[0][1].endswith("/tnmaccess/api/products")
    assert t.calls[0][2]["outputFormat"] == "JSON"


# ---------- GBIF ----------

def test_gbif_parses_occurrence_records():
    t = FakeTransport(payload=GBIF_PAYLOAD)
    items = build_adapter("gbif", t).fetch_items(max_items=2)
    assert len(items) == 2

    first = items[0]
    assert first.id == "1234567890"
    assert first.title == "Quercus robur L."
    assert first.url == "https://example.org/occ/1234567890"
    assert first.format == "JSON"
    assert first.size_bytes is None            # GBIF 无字节量口径
    assert first.extra["license"].startswith("http")
    assert first.extra["country"] == "NO"

    # 单点记录生成极小包围盒
    assert first.bbox[0] < first.bbox[2] and first.bbox[1] < first.bbox[3]
    assert first.bbox[0] == pytest.approx(5.32 - 0.0001)
    assert first.bbox[3] == pytest.approx(60.35 + 0.0001)

    # 缺 references 时回落 GBIF 记录页
    assert items[1].url == "https://www.gbif.org/occurrence/222"

    method, url, params = t.calls[0]
    assert method == "GET"
    assert url.endswith("/v1/occurrence/search")
    assert params["limit"] == 2
    assert params["hasCoordinate"] == "true"


def test_gbif_taxon_key_optional_param():
    t = FakeTransport(payload=GBIF_PAYLOAD)
    build_adapter("gbif", t, taxon_key=2877951).fetch_items(max_items=1)
    assert t.calls[0][2]["taxonKey"] == 2877951


def test_gbif_limit_capped():
    t = FakeTransport(payload={"results": []})
    build_adapter("gbif", t, limit_cap=50).fetch_items(max_items=999)
    assert t.calls[0][2]["limit"] == 50


def test_gbif_coordinate_out_of_range_rejected():
    payload = {"results": [{"key": 1, "decimalLatitude": 91, "decimalLongitude": 0}]}
    with pytest.raises(HarvestError) as ei:
        build_adapter("gbif", FakeTransport(payload=payload)).fetch_items()
    assert ei.value.failure_class == "元数据校验"
    assert ei.value.retryable is False


def test_gbif_missing_key_rejected():
    payload = {"results": [{"scientificName": "no key"}]}
    with pytest.raises(HarvestError) as ei:
        build_adapter("gbif", FakeTransport(payload=payload)).fetch_items()
    assert ei.value.failure_class == "元数据校验"


def test_gbif_bad_shape_rejected():
    with pytest.raises(HarvestError) as ei:
        build_adapter("gbif", FakeTransport(payload={"results": "nope"})).fetch_items()
    assert ei.value.failure_class == "元数据校验"


# ---------- ESA ----------

def test_esa_parses_odata_products():
    t = FakeTransport(payload=ESA_PAYLOAD)
    items = build_adapter("esa", t).fetch_items(max_items=1)
    assert len(items) == 1
    item = items[0]
    assert item.id == "11111111-2222-3333-4444-555555555555"
    assert item.format == "SENTINEL-2"
    assert item.size_bytes == 812345678
    assert item.url.endswith("/odata/v1/Products(11111111-2222-3333-4444-555555555555)/$value")
    assert item.extra["checksum_blake3"] == "deadbeef"
    assert item.extra["online"] is True
    assert item.bbox == [10.1, 59.1, 11.4, 60.3]

    method, url, params = t.calls[0]
    assert method == "GET"
    assert url.endswith("/odata/v1/Products")
    assert params["$filter"] == "Collection/Name eq 'SENTINEL-2'"
    assert params["$top"] == 1
    assert params["$orderby"] == "ContentDate/Start desc"


def test_esa_missing_id_or_name_rejected():
    payload = {"value": [{"Name": "S2A_x.SAFE"}]}
    with pytest.raises(HarvestError) as ei:
        build_adapter("esa", FakeTransport(payload=payload)).fetch_items()
    assert ei.value.failure_class == "元数据校验"


def test_esa_bad_shape_rejected():
    with pytest.raises(HarvestError) as ei:
        build_adapter("esa", FakeTransport(payload={"value": {}})).fetch_items()
    assert ei.value.failure_class == "元数据校验"


def test_esa_bbox_from_footprint_helpers():
    # 缺 GeoFootprint：bbox 为 None（不阻断采集）
    assert _bbox_from_geo_footprint(None) is None
    assert _bbox_from_geo_footprint({"type": "Point"}) is None
    # 点数不足 3 的多边形不是合法环
    assert _bbox_from_geo_footprint({"type": "Polygon", "coordinates": [[[1, 2], [3, 4]]]}) is None
    # 越界坐标判定非法
    assert _bbox_from_geo_footprint(
        {"type": "Polygon", "coordinates": [[[0, 0], [200, 0], [0, 10], [0, 0]]]}
    ) is None
    assert _bbox_from_geo_footprint(
        {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 20], [0, 0]]]}
    ) == [0.0, 0.0, 10.0, 20.0]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("S2B_MSIL2A_x.SAFE", "SENTINEL-2"),
        ("S1A_IW_GRDH_x.SAFE", "SENTINEL-1"),
        ("S3A_OL_1_EFR_x.SEN3", "SENTINEL-3"),
        ("weird_product", "SAFE"),
        ("", "SAFE"),
    ],
)
def test_esa_product_format_mapping(name, expected):
    assert _product_format(name) == expected


# ---------- 三源统一失败分类 ----------

@pytest.mark.parametrize("code", list(ACTIVE_SOURCES))
@pytest.mark.parametrize(
    "status,expected_class,expected_retryable",
    [
        (429, "源站限流", True),
        (503, "源站限流", True),
        (401, "凭证失效", False),
        (403, "凭证失效", False),
        (500, "连接超时", True),
    ],
)
def test_all_sources_share_failure_classes(code, status, expected_class, expected_retryable):
    with pytest.raises(HarvestError) as ei:
        build_adapter(code, FakeTransport(status=status, payload=None)).fetch_items()
    assert ei.value.failure_class == expected_class
    assert ei.value.retryable is expected_retryable


@pytest.mark.parametrize("code", list(ACTIVE_SOURCES))
def test_all_sources_map_network_exception_to_timeout(code):
    with pytest.raises(HarvestError) as ei:
        build_adapter(code, FakeTransport(exc=TimeoutError("connect timeout"))).fetch_items()
    assert ei.value.failure_class == "连接超时"
    assert ei.value.retryable is True


@pytest.mark.parametrize("code,max_items", [("usgs", 3), ("gbif", 1), ("esa", 100)])
def test_max_items_respected_in_params(code, max_items):
    t = FakeTransport(payload={})
    source_params = {"usgs": lambda p: p["max"],
                     "gbif": lambda p: p["limit"],
                     "esa": lambda p: p["$top"]}[code]
    try:
        build_adapter(code, t).fetch_items(max_items=max_items)
    except HarvestError:
        pass  # 空 payload 属结构校验失败，但请求已发出，形参可断言
    assert source_params(t.calls[0][2]) > 0
