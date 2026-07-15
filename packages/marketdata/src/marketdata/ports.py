"""解耦端口:宿主实现这两个 Protocol 即可接入,包本身不依赖 web/DB。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class SourceConfig:
    """一个数据源的运行配置(由 ConfigProvider 提供)。"""

    vendor: str
    priority: int = 100
    enabled: bool = True
    config: dict = field(default_factory=dict)   # 凭证/参数:token / cookies / proxy ...
    supports_batch: bool = False


@runtime_checkable
class ConfigProvider(Protocol):
    def sources_for(self, datatype: str, market: str | None) -> list[SourceConfig]:
        """返回该 datatype 在该 market 下、按优先级排序的源列表。"""
        ...


@runtime_checkable
class MetricsSink(Protocol):
    def record(self, *, vendor: str, datatype: str, market: str | None,
               ok: bool, count: int, latency_ms: int, error: str = "") -> None:
        ...
