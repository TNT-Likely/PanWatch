"""对象式入口:注入 ConfigProvider(+可选 MetricsSink),对外提供 quotes()/health()。"""

from __future__ import annotations

from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.ports import ConfigProvider, MetricsSink
from marketdata.symbol import Symbol
from marketdata.types import CapitalFlow, EventItem, HotBoard, HotStock, Quote, Request
from marketdata.vendors.discovery import DiscoveryVendor
from marketdata.vendors.tencent import TencentQuoteVendor
from marketdata.vendors.yfinance import YFinanceQuoteVendor


class MarketData:
    def __init__(self, config: ConfigProvider, metrics: MetricsSink | None = None):
        self.config = config
        self.metrics = metrics or InMemoryMetricsSink()
        self._quote_engine = Engine(
            datatype="quote",
            vendors={
                "tencent": TencentQuoteVendor(),
                "yfinance": YFinanceQuoteVendor(),
            },
            config=config,
            metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=5.0),
            default_ttl=5.0,
        )
        from marketdata.vendors.kline import TencentKlineVendor, StooqKlineVendor, EastmoneyKlineVendor
        self._kline_engine = Engine(
            datatype="kline",
            vendors={"tencent": TencentKlineVendor(), "stooq": StooqKlineVendor(),
                     "eastmoney": EastmoneyKlineVendor()},
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        from marketdata.vendors.capital_flow import EastmoneyCapitalFlowVendor
        self._capital_flow_engine = Engine(
            datatype="capital_flow",
            vendors={"eastmoney": EastmoneyCapitalFlowVendor()},
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        from marketdata.vendors.events import EventsVendor
        self._events_engine = Engine(
            datatype="events",
            vendors={"eastmoney": EventsVendor()},
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        # discovery(东财热门榜)是市场级、单源、非 symbol 模型,不进 Engine/不进 DataSource
        # taxonomy —— md 直接委托给 DiscoveryVendor。
        self._discovery = DiscoveryVendor()

    def klines(self, symbol: str, *, market: str, days: int = 120, min_count: int = 1) -> list:
        """按 priority 主备取日K(不足则试下一个,全不足取最长)。返回 list[Bar]。
        不在包内缓存(cache_ttl_sec=0);宿主自行缓存。"""
        req = Request(symbols=(symbol,), market=market, timeframe="day", limit=days,
                      extra=(("days", days),))
        resp = self._kline_engine.fetch(req, min_count=min_count, cache_ttl_sec=0)
        return resp.data or []

    def quotes(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[Quote]:
        """批量报价。symbols 可跨市场:未显式给 market 时按代码自动识别并分组。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[Quote] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._quote_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def capital_flow(self, symbol: str, *, market: str = "CN") -> CapitalFlow | None:
        """单只股票资金流向。不在包内缓存(cache_ttl_sec=0);宿主自行缓存。"""
        req = Request(symbols=(symbol,), market=market)
        resp = self._capital_flow_engine.fetch(req, cache_ttl_sec=0)
        data = resp.data or []
        return data[0] if data else None

    def events(self, symbols: list[str], *, market: str = "CN", since_days: int = 7) -> list[EventItem]:
        """结构化事件(东财公告)。批量 symbols。不在包内缓存(cache_ttl_sec=0);宿主自行缓存。"""
        req = Request(symbols=tuple(symbols), market=market, since_hours=since_days * 24,
                      extra=(("since_days", since_days),))
        resp = self._events_engine.fetch(req, cache_ttl_sec=0)
        return resp.data or []

    def health(self) -> dict[str, dict]:
        """每个 vendor 的内存健康度快照(成功率 / p50 延迟 / 最近错误)。"""
        return self.metrics.snapshot()

    def hot_stocks(self, **kw) -> list[HotStock]:
        """热门/异动股(东财榜单,市场级、不经 Engine)。"""
        return self._discovery.hot_stocks(**kw)

    def hot_boards(self, **kw) -> list[HotBoard]:
        """热门板块(东财榜单,市场级、不经 Engine)。"""
        return self._discovery.hot_boards(**kw)

    def board_stocks(self, **kw) -> list[HotStock]:
        """板块成分股榜单(东财,市场级、不经 Engine)。"""
        return self._discovery.board_stocks(**kw)
