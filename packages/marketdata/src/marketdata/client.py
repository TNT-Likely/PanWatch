"""对象式入口:注入 ConfigProvider(+可选 MetricsSink),对外提供 quotes()/health()。"""

from __future__ import annotations

from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.ports import ConfigProvider, MetricsSink
from marketdata.registry import build_vendors
from marketdata.symbol import Symbol
from marketdata.types import (
    CapitalFlow,
    DividendItem,
    DragonTigerItem,
    EventItem,
    FlashNews,
    Fundamentals,
    HotBoard,
    HotStock,
    MarginItem,
    NorthboundItem,
    Quote,
    Request,
    ShareholderItem,
)
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
        # flash_news(快讯 7×24)是市场级(symbols 恒空),但仍走 Engine 做主备/缓存/健康度,
        # 与 discovery(不进 Engine)的区别是:flash_news 有多源竞争、需要统一 TTL 缓存。
        self._flash_news_engine = Engine(
            datatype="flash_news",
            vendors=build_vendors("flash_news"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=30.0), default_ttl=30.0,
        )
        # discovery(东财热门榜)是市场级、单源、非 symbol 模型,不进 Engine/不进 DataSource
        # taxonomy —— md 直接委托给 DiscoveryVendor。
        self._discovery = DiscoveryVendor()
        self._fundamentals_engine = Engine(
            datatype="fundamentals",
            vendors=build_vendors("fundamentals"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        # 龙虎榜/融资融券/股东户数/分红:市场/资金面,均走东财 datacenter 同构接口,
        # 更新频率低(日频/期频),沿用 fundamentals 同款 300s TTL。
        self._dragon_tiger_engine = Engine(
            datatype="dragon_tiger",
            vendors=build_vendors("dragon_tiger"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        self._margin_engine = Engine(
            datatype="margin",
            vendors=build_vendors("margin"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        self._shareholders_engine = Engine(
            datatype="shareholders",
            vendors=build_vendors("shareholders"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        self._dividend_engine = Engine(
            datatype="dividend",
            vendors=build_vendors("dividend"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        # 北向资金(同花顺 hexin 当日分钟累计净买入):市场级、单源,更新频率为分钟级
        # 但当日累计值短期内变化不大,沿用 flash_news 同款 60s TTL(比 300s 更贴合"盘中递增")。
        self._northbound_engine = Engine(
            datatype="northbound",
            vendors=build_vendors("northbound"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=60.0), default_ttl=60.0,
        )

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

    def flash_news(self, *, market: str = "CN", limit: int = 50, keyword: str | None = None) -> list[FlashNews]:
        """快讯(7×24)。市场级,symbols 恒空。不在包内缓存额外一层——用 Engine 默认 30s TTL。"""
        req = Request(symbols=(), market=market, limit=limit)
        resp = self._flash_news_engine.fetch(req)
        data = resp.data or []
        if keyword:
            data = [x for x in data if keyword in (x.title or "") or keyword in (x.content or "")]
        return data

    def fundamentals(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[Fundamentals]:
        """批量基本面/财务(按 symbol)。symbols 可跨市场:未显式给 market 时按代码自动识别并分组。
        照 quotes() 范式:按市场分组、每组建 Request、逐组 engine.fetch、合并结果。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[Fundamentals] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._fundamentals_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def dragon_tiger(self, *, date: str | None = None, market: str = "CN") -> list[DragonTigerItem]:
        """龙虎榜(市场级,单日快照)。date 未给出时不猜测"今天",直接返回 []。"""
        req = Request(symbols=(), market=market, extra=(("date", date),))
        resp = self._dragon_tiger_engine.fetch(req)
        return resp.data or []

    def margin(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[MarginItem]:
        """批量融资融券(按 symbol,取每只最新一条快照)。照 fundamentals() 分组范式。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[MarginItem] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._margin_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def shareholders(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[ShareholderItem]:
        """批量股东户数(按 symbol,取每只最新一期)。照 fundamentals() 分组范式。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[ShareholderItem] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._shareholders_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def dividend(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[DividendItem]:
        """批量分红(按 symbol,返回每只全部历史)。照 fundamentals() 分组范式。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[DividendItem] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._dividend_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def northbound(self, *, market: str = "CN") -> list[NorthboundItem]:
        """北向资金(市场级,symbols 恒空)。照 flash_news() 无 symbols 范式。"""
        req = Request(symbols=(), market=market)
        resp = self._northbound_engine.fetch(req)
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
