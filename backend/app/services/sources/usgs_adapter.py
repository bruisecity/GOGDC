"""USGS 适配器（W6：把既有 USGSTNMClient 纳入多源注册表）。

W2 的 USGSTNMClient 已实现 TNM products 契约与四类失败分类，
本适配器只做协议对齐（code + fetch_items），不改原客户端行为——
W2–W5 的调用路径与测试全部保持有效。
"""
from __future__ import annotations

from ..usgs import HarvestItem, USGSTNMClient


class USGSAdapter:
    code = "usgs"

    def __init__(self, transport, *, base_url: str | None = None, datasets: str | None = None) -> None:
        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        if datasets:
            kwargs["datasets"] = datasets
        self._client = USGSTNMClient(transport, **kwargs)

    def fetch_items(self, max_items: int = 10) -> list[HarvestItem]:
        return self._client.fetch_products(max_items=max_items)
