"""多源适配器注册表（W6：3 源并行采集）。

按 data_source.code 取适配器：usgs / gbif / esa。
未注册的数据源一律报「元数据校验」失败（不可重试），不静默降级到别源。
"""
from __future__ import annotations

from ..usgs import HarvestError
from .base import HarvestItem, SourceAdapter
from .esa import ESAAdapter
from .gbif import GBIFAdapter
from .usgs_adapter import USGSAdapter

ADAPTERS: dict[str, type] = {
    "usgs": USGSAdapter,
    "gbif": GBIFAdapter,
    "esa": ESAAdapter,
}

# W6 收窄后的在役源（WorldPop 延至试运行期，见设计稿风险条目）
ACTIVE_SOURCES: tuple[str, ...] = ("usgs", "gbif", "esa")


def build_adapter(code: str, transport, **kwargs) -> SourceAdapter:
    """按源编码构造适配器；未注册源报不可重试的元数据校验失败。"""
    cls = ADAPTERS.get(code)
    if cls is None:
        raise HarvestError(
            "元数据校验",
            f"未注册的数据源适配器: {code!r}（已注册: {sorted(ADAPTERS)}）",
            retryable=False,
        )
    return cls(transport, **kwargs)
