"""多源采集适配层基础（W6 切片：USGS / GBIF / ESA 统一协议）。

口径：
- 每个数据源实现 `SourceAdapter`（`fetch_items(max_items)` 返回 HarvestItem 列表），
  传输层构造注入，测试用假 transport 离线跑；
- 失败仍按设计稿四类分类（连接超时 / 源站限流 / 凭证失效 / 元数据校验），
  由 `classify_http_status` 统一映射，各适配器只负责响应体结构解析；
- `HarvestItem` 在 W6 增加可选 `extra`（源侧附加溯源字段，不参与落盘契约），
  向后兼容 W2–W5 的构造方式。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional, Protocol

from ..usgs import FAILURE_CLASSES, HarvestError, HarvestItem  # noqa: F401 统一出口
from ..usgs import classify_http_status

__all__ = [
    "FAILURE_CLASSES",
    "HarvestError",
    "HarvestItem",
    "SourceAdapter",
    "classify_http_status",
    "with_extra",
]


class SourceAdapter(Protocol):
    """数据源适配器协议：拉取至多 max_items 条目录条目。"""

    code: str

    def fetch_items(self, max_items: int = 10) -> list[HarvestItem]: ...


def with_extra(item: HarvestItem, extra: dict) -> HarvestItem:
    """附加源侧溯源字段（HarvestItem 为 dataclass，extra 为 W6 新增可选字段）。"""
    if hasattr(item, "extra"):
        return replace(item, extra=extra)
    return item
