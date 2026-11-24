"""USGS TNM Access API 适配器（W2 切片 0 数据源）。

设计要点：
- HTTP transport 可注入（构造函数注入），测试用假 transport 离线跑，
  生产由 worker 注入 httpx 实现——沙箱/汇聚节点出网能力不确定，
  采集逻辑不与具体 HTTP 客户端耦合（设计稿第四章「出网能力」风险）。
- 失败分类严格对齐设计稿四类：连接超时 / 源站限流 / 凭证失效 / 元数据校验，
  并给出是否可重试判定（retry_count 状态机据此驱动）。

TNM 契约按公开文档口径实现：GET /tnmaccess/api/products 返回
{"products": [ {title, downloadURL/sourceUri, sizeInBytes, format, ...} ]}。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

FAILURE_CLASSES = ("连接超时", "源站限流", "凭证失效", "元数据校验")


class HarvestError(Exception):
    """采集失败，携带设计稿口径的失败分类与可重试性。"""

    def __init__(self, failure_class: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        assert failure_class in FAILURE_CLASSES, f"未知失败分类: {failure_class}"
        self.failure_class = failure_class
        self.message = message
        self.retryable = retryable


@dataclass
class HarvestItem:
    """一条可下载的源数据条目（W2 切片只采集目录，落地在 W3 扩展）。"""

    id: str
    title: str
    url: str
    size_bytes: Optional[int]
    format: Optional[str]


class Transport(Protocol):
    """传输层协议：method/url/params -> (status_code, parsed_json_or_None)。"""

    def request(self, method: str, url: str, params: dict) -> tuple[int, Optional[Any]]:
        ...


def classify_http_status(status_code: int) -> Optional[HarvestError]:
    """HTTP 状态码 → 失败分类（None 表示可继续解析响应体）。"""
    if status_code in (401, 403):
        return HarvestError("凭证失效", f"源站拒绝访问: HTTP {status_code}", retryable=False)
    if status_code == 429 or status_code == 503:
        return HarvestError("源站限流", f"源站限流: HTTP {status_code}", retryable=True)
    if status_code >= 500:
        return HarvestError("连接超时", f"源站异常: HTTP {status_code}", retryable=True)
    if status_code != 200:
        return HarvestError("元数据校验", f"意外状态码: HTTP {status_code}", retryable=False)
    return None


class USGSTNMClient:
    """TNM products 端点客户端。"""

    def __init__(
        self,
        transport: Transport,
        base_url: str = "https://apps.nationalmap.gov",
        datasets: str = "National Elevation Dataset (NED) 1/3 arc-second",
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._datasets = datasets

    def fetch_products(self, max_items: int = 10) -> list[HarvestItem]:
        """拉取产品目录；任何失败按四类分类抛 HarvestError。"""
        url = f"{self._base_url}/tnmaccess/api/products"
        params = {
            "datasets": self._datasets,
            "outputFormat": "JSON",
            "max": max_items,
        }
        try:
            status, payload = self._transport.request("GET", url, params)
        except HarvestError:
            raise
        except Exception as exc:  # 网络层异常统一归「连接超时」
            raise HarvestError(
                "连接超时", f"请求失败: {type(exc).__name__}: {exc}", retryable=True
            ) from exc

        failure = classify_http_status(status)
        if failure is not None:
            raise failure

        # 结构校验：顶层 dict + products 列表，条目含可定位的下载 URL
        if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
            raise HarvestError(
                "元数据校验",
                f"响应结构不符预期: {type(payload).__name__}",
                retryable=False,
            )
        items: list[HarvestItem] = []
        for i, entry in enumerate(payload["products"]):
            if not isinstance(entry, dict):
                raise HarvestError(
                    "元数据校验", f"products[{i}] 不是对象", retryable=False
                )
            url_value = entry.get("downloadURL") or entry.get("downloadUrl") or entry.get("sourceUri")
            if not url_value:
                raise HarvestError(
                    "元数据校验",
                    f"products[{i}] 缺少下载地址字段",
                    retryable=False,
                )
            items.append(
                HarvestItem(
                    id=str(entry.get("id") or entry.get("sourceId") or i),
                    title=str(entry.get("title") or entry.get("name") or ""),
                    url=str(url_value),
                    size_bytes=entry.get("sizeInBytes"),
                    format=entry.get("format"),
                )
            )
        return items
