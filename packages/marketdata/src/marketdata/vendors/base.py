"""Vendor 抽象:每个 vendor 只负责"一家源怎么抓 + 解析成标准类型",内部无 fallback。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from marketdata.symbol import Symbol


class Vendor(ABC):
    #: 注册名,与 SourceConfig.vendor / DataSource.provider 对齐
    name: str = ""
    #: 支持的市场集合(空集=全部);Engine 会按市场过滤
    supports_markets: set[str] = set()

    @abstractmethod
    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        """抓取并解析。失败应抛异常(Engine 捕获后转移),空结果返回 []。"""
        ...


class QuoteVendor(Vendor):
    """报价 vendor:fetch 返回 list[Quote]。"""

    pass


class KlineVendor(Vendor):
    """K 线 vendor:fetch 返回 list[Bar]。单 symbol。"""

    pass


class CapitalFlowVendor(Vendor):
    """资金流向 vendor:fetch 返回 list[CapitalFlow]。单 symbol。"""

    pass


class EventsVendor(Vendor):
    """事件 vendor:fetch 返回 list[EventItem]。批量(多 symbol)。"""

    pass


class FlashNewsVendor(Vendor):
    """快讯 vendor:fetch 返回 list[FlashNews]。市场级,symbols 可空。"""

    pass


class NewsVendor(Vendor):
    """新闻 vendor:返回 list[NewsArticle],按 symbol。"""

    pass


class FundamentalsVendor(Vendor):
    """基本面/财务 vendor:fetch 返回 list[Fundamentals]。按 symbol(批量)。"""

    pass


class DragonTigerVendor(Vendor):
    """龙虎榜 vendor:fetch 返回 list[DragonTigerItem]。市场级(symbols 恒空),按 date 过滤。"""

    pass


class MarginVendor(Vendor):
    """融资融券 vendor:fetch 返回 list[MarginItem]。按 symbol(逐只取最新快照)。"""

    pass


class ShareholdersVendor(Vendor):
    """股东户数 vendor:fetch 返回 list[ShareholderItem]。按 symbol(逐只取最新一期)。"""

    pass


class DividendVendor(Vendor):
    """分红 vendor:fetch 返回 list[DividendItem]。按 symbol(逐只返回全部历史)。"""

    pass


class NorthboundVendor(Vendor):
    """北向资金 vendor:fetch 返回 list[NorthboundItem]。市场级(symbols 可空)。"""

    pass
