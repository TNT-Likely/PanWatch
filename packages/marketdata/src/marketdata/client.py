"""对象式入口:注入 ConfigProvider(+可选 MetricsSink),对外提供 quotes()/health()。"""

from __future__ import annotations

from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.ports import ConfigProvider, MetricsSink
from marketdata.registry import build_vendors
from marketdata.symbol import Symbol
from marketdata.types import CapitalFlow, EventItem, HotBoard, HotStock, Quote, Request
from marketdata.vendors.discovery import DiscoveryVendor

# 指数 secid(东财):指数与个股 secid 前缀规则不同,必须显式映射,否则按个股规则会取错标的。
# 美股指数东财K线不支持,未列入 → index_klines 返回空,fail-soft。
INDEX_SECID: dict[str, str] = {
    "000300": "1.000300",   # 沪深300
    "000001": "1.000001",   # 上证指数
    "399001": "0.399001",   # 深证成指
    "399006": "0.399006",   # 创业板指
    "HSI": "100.HSI",       # 恒生指数
}


class MarketData:
    def __init__(self, config: ConfigProvider, metrics: MetricsSink | None = None):
        self.config = config
        self.metrics = metrics or InMemoryMetricsSink()
        self._quote_engine = Engine(
            datatype="quote",
            vendors=build_vendors("quote"),
            config=config,
            metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=5.0),
            default_ttl=5.0,
        )
        self._kline_engine = Engine(
            datatype="kline",
            vendors=build_vendors("kline"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        self._capital_flow_engine = Engine(
            datatype="capital_flow",
            vendors=build_vendors("capital_flow"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        self._events_engine = Engine(
            datatype="events",
            vendors=build_vendors("events"),
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

    def index_quotes(self, tencent_symbols: list[str]) -> list[dict]:
        """按原始腾讯指数符号(sh000001/hkHSI/usDJI…)取行情,不经 Symbol.parse。

        指数代码可能与个股代码撞号(如 000001 既是平安银行又是上证指数),故走显式符号路径。
        返回 list[dict]。
        """
        from marketdata.vendors.tencent import fetch_raw
        return fetch_raw(list(tencent_symbols)) if tencent_symbols else []

    def index_klines(self, code: str, *, market: str, days: int = 120) -> list:
        """指数日K:INDEX_SECID 显式映射走东财;未映射(如美股指数)→ [](fail-soft)。返回 list[Bar]。"""
        secid = INDEX_SECID.get(str(code).strip()) or INDEX_SECID.get(str(code).strip().upper())
        if not secid:
            return []
        from marketdata.vendors.kline import fetch_eastmoney_kline
        return fetch_eastmoney_kline(secid, days)

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
