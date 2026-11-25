"""GBIF 采集适配器（W6 切片：物种出现记录 occurrence/search）。

契约核实自公开 API（2026-11 实测）：
GET /v1/occurrence/search?limit=N&hasCoordinate=true[&taxonKey=..]
→ {"count": int, "endOfRecords": bool, "results": [
     {key, scientificName, decimalLatitude, decimalLongitude,
      license, references, year, basisOfRecord, country, ...}]}

落盘映射：
- id      = gbifID 或 key
- url     = references 的下载/溯源地址；缺失时回落 GBIF 记录页
            https://www.gbif.org/occurrence/{key}
- format  = "JSON"（记录为结构化元数据，非栅格影像）
- bbox    = 单点记录按极小包围盒（经纬度 ±0.0001）表示，便于编目空间过滤
"""
from __future__ import annotations

from ..usgs import HarvestError, HarvestItem, classify_http_status


class GBIFAdapter:
    code = "gbif"

    def __init__(
        self,
        transport,
        base_url: str = "https://api.gbif.org",
        taxon_key: int | None = None,
        limit_cap: int = 300,
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._taxon_key = taxon_key
        self._limit_cap = limit_cap

    def fetch_items(self, max_items: int = 10) -> list[HarvestItem]:
        limit = max(1, min(max_items, self._limit_cap))
        params: dict = {"limit": limit, "hasCoordinate": "true"}
        if self._taxon_key is not None:
            params["taxonKey"] = self._taxon_key
        try:
            status, payload = self._transport.request(
                "GET", f"{self._base_url}/v1/occurrence/search", params
            )
        except HarvestError:
            raise
        except Exception as exc:
            raise HarvestError(
                "连接超时", f"GBIF 请求失败: {type(exc).__name__}: {exc}", retryable=True
            ) from exc

        failure = classify_http_status(status)
        if failure is not None:
            raise failure

        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise HarvestError(
                "元数据校验",
                f"GBIF 响应结构不符预期: {type(payload).__name__}",
                retryable=False,
            )

        items: list[HarvestItem] = []
        for i, entry in enumerate(payload["results"]):
            if not isinstance(entry, dict):
                raise HarvestError("元数据校验", f"results[{i}] 不是对象", retryable=False)
            key = entry.get("key") or entry.get("gbifID") or entry.get("gbifId")
            if key is None:
                raise HarvestError(
                    "元数据校验", f"results[{i}] 缺少 key/gbifID", retryable=False
                )
            lat, lon = entry.get("decimalLatitude"), entry.get("decimalLongitude")
            bbox = None
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise HarvestError(
                        "元数据校验",
                        f"results[{i}] 坐标越界: ({lat}, {lon})",
                        retryable=False,
                    )
                eps = 0.0001
                bbox = [lon - eps, lat - eps, lon + eps, lat + eps]
            url = entry.get("references") or f"https://www.gbif.org/occurrence/{key}"
            items.append(
                HarvestItem(
                    id=str(key),
                    title=str(entry.get("scientificName") or entry.get("acceptedScientificName") or ""),
                    url=str(url),
                    size_bytes=None,  # GBIF 记录无字节量口径
                    format="JSON",
                    bbox=bbox,
                    extra={
                        "scientific_name": entry.get("scientificName"),
                        "license": entry.get("license"),
                        "basis_of_record": entry.get("basisOfRecord"),
                        "country": entry.get("country"),
                        "year": entry.get("year"),
                    },
                )
            )
        return items
