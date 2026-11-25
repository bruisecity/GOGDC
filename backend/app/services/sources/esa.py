"""ESA 采集适配器（W6 切片：Copernicus Data Space OData Products）。

契约核实自公开 API（2026-11 实测）：
GET /odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2'
    &$top=N&$orderby=ContentDate/Start desc
→ {"value": [{Id, Name, S3Path, ContentLength, ContentDate:{Start,End},
              Online, GeoFootprint:{type:'Polygon',coordinates:[[[lon,lat],...]]},
              Checksum:[{Value,Algorithm}], ...}]}

落盘映射：
- id      = Id（GUID，稳定）
- url     = https://catalogue.dataspace.copernicus.eu/odata/v1/Products({Id})/$value
            （官方产品下载入口）
- format  = 产品类型（Name 前缀归一，如 SENTINEL-2 / SENTINEL-1）；缺省 "SAFE"
- size    = ContentLength（字节）
- bbox    = GeoFootprint 多边形外接矩形 [west, south, east, north]
"""
from __future__ import annotations

from ..usgs import HarvestError, HarvestItem, classify_http_status

DOWNLOAD_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products({id})/$value"


def _bbox_from_geo_footprint(fp) -> list | None:
    """GeoFootprint(GeoJSON Polygon) → WGS84 外接矩形。"""
    if not isinstance(fp, dict) or fp.get("type") != "Polygon":
        return None
    rings = fp.get("coordinates")
    if not isinstance(rings, (list, tuple)) or not rings:
        return None
    points = rings[0]
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        return None
    lons, lats = [], []
    for pt in points:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            return None
        lon, lat = pt[0], pt[1]
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            return None
        lons.append(float(lon))
        lats.append(float(lat))
    west, east, south, north = min(lons), max(lons), min(lats), max(lats)
    if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
        return None
    return [west, south, east, north]


def _product_format(name: str) -> str:
    if not name:
        return "SAFE"
    head = name.split("_", 1)[0].upper()
    if head.startswith("S1"):
        return "SENTINEL-1"
    if head.startswith("S2"):
        return "SENTINEL-2"
    if head.startswith("S3"):
        return "SENTINEL-3"
    return "SAFE"


class ESAAdapter:
    code = "esa"

    def __init__(
        self,
        transport,
        base_url: str = "https://catalogue.dataspace.copernicus.eu",
        collection: str = "SENTINEL-2",
        top_cap: int = 100,
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._collection = collection
        self._top_cap = top_cap

    def fetch_items(self, max_items: int = 10) -> list[HarvestItem]:
        top = max(1, min(max_items, self._top_cap))
        params = {
            "$filter": f"Collection/Name eq '{self._collection}'",
            "$top": top,
            "$orderby": "ContentDate/Start desc",
        }
        try:
            status, payload = self._transport.request(
                "GET", f"{self._base_url}/odata/v1/Products", params
            )
        except HarvestError:
            raise
        except Exception as exc:
            raise HarvestError(
                "连接超时", f"ESA 请求失败: {type(exc).__name__}: {exc}", retryable=True
            ) from exc

        failure = classify_http_status(status)
        if failure is not None:
            raise failure

        if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
            raise HarvestError(
                "元数据校验",
                f"ESA 响应结构不符预期: {type(payload).__name__}",
                retryable=False,
            )

        items: list[HarvestItem] = []
        for i, entry in enumerate(payload["value"]):
            if not isinstance(entry, dict):
                raise HarvestError("元数据校验", f"value[{i}] 不是对象", retryable=False)
            product_id = entry.get("Id")
            name = entry.get("Name")
            if not product_id or not name:
                raise HarvestError(
                    "元数据校验", f"value[{i}] 缺少 Id/Name", retryable=False
                )
            length = entry.get("ContentLength")
            size = int(length) if isinstance(length, (int, float)) else None
            checksum = entry.get("Checksum")
            checksum_value = None
            if isinstance(checksum, list):
                for c in checksum:
                    if isinstance(c, dict) and c.get("Algorithm") == "BLAKE3":
                        checksum_value = c.get("Value")
                        break
            items.append(
                HarvestItem(
                    id=str(product_id),
                    title=str(name),
                    url=DOWNLOAD_URL.format(id=product_id),
                    size_bytes=size,
                    format=_product_format(name),
                    bbox=_bbox_from_geo_footprint(entry.get("GeoFootprint")),
                    extra={
                        "collection": self._collection,
                        "s3_path": entry.get("S3Path"),
                        "online": entry.get("Online"),
                        "checksum_blake3": checksum_value,
                    },
                )
            )
        return items
