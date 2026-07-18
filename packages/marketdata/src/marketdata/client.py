"""对象式入口:注入 ConfigProvider(+可选 MetricsSink),对外提供 quotes()/health()。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.http import record_error
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
    NewsArticle,
    NorthboundItem,
    Quote,
    Request,
    ShareholderItem,
)
from marketdata.vendors.discovery import DiscoveryVendor
from marketdata.vendors.news import EastmoneyStockNewsVendor

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
        # news(新闻资讯)是聚合语义(并发查所有已启用源、结果合并去重),非失败转移
        # (找到一个就停),硬套 Engine 的主备模型是设计错配,故不进 Engine —— 只借 registry
        # 的 build_vendors 复用 vendor 实例,合并/去重/排序/since 过滤逻辑在 news() 里自己做。
        self._news_vendors = build_vendors("news")
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

    def news(
        self,
        symbols: list[str],
        *,
        market: str = "CN",
        since_hours: int = 2,
        names: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> list[NewsArticle]:
        """新闻资讯(个股新闻 + 公告)—— 聚合语义,非失败转移:查询所有已启用源、结果合并去重,
        而非"找到一个就停"(这与 quotes()/klines() 的主备语义不同),故不经 Engine。

        对齐 PanWatch NewsCollector.fetch_all 的聚合语义:
        - 公告源(vendor="eastmoney")用 max(since_hours, 72) 更宽窗口(公告发布频率低,
          窗口太窄容易一条都捞不到);其余源用 since_hours。窗口值会透传进 vendor 的
          config(当前 3 个 vendor 均未读取——真正的 since 过滤在本方法做,vendor 内
          不允许调用无参 datetime.now())。
        - 合并后按 external_id 去重,保留先出现的(即优先级更高的源优先保留)。
        - 按 publish_time 倒序排列。
        - since 过滤需要"当下"锚点:传 now 才过滤(每条按其来源选窗口,规则同上);
          不传 now 则不过滤,原样返回全部合并结果(包内绝不偷偷调 datetime.now())。
        """
        syms = [Symbol.parse(s, market) for s in symbols]
        srcs = sorted(self.config.sources_for("news", market), key=lambda s: s.priority)

        all_articles: list[NewsArticle] = []
        for src in srcs:
            if not src.enabled:
                continue
            vendor = self._news_vendors.get(src.vendor)
            if vendor is None:
                continue
            if vendor.supports_markets and market not in vendor.supports_markets:
                continue

            window = max(since_hours, 72) if src.vendor == "eastmoney" else since_hours
            call_config = {**(src.config or {}), "symbol_names": names or {}, "since_hours": window}

            t0 = time.monotonic()
            try:
                articles = vendor.fetch(syms, call_config) or []
            except Exception as e:
                latency = int((time.monotonic() - t0) * 1000)
                self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                    ok=False, count=0, latency_ms=latency, error=str(e))
                record_error(f"{src.vendor}: {type(e).__name__}: {e}")
                continue

            latency = int((time.monotonic() - t0) * 1000)
            if articles:
                self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                    ok=True, count=len(articles), latency_ms=latency)
            else:
                self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                    ok=False, count=0, latency_ms=latency, error="empty")
            all_articles.extend(articles)

        seen: set[str] = set()
        deduped: list[NewsArticle] = []
        for a in all_articles:
            if a.external_id in seen:
                continue
            seen.add(a.external_id)
            deduped.append(a)

        deduped.sort(key=lambda a: a.publish_time, reverse=True)

        if now is not None:
            def _keep(a: NewsArticle) -> bool:
                w = max(since_hours, 72) if a.source == "eastmoney" else since_hours
                return a.publish_time >= now - timedelta(hours=w)
            deduped = [a for a in deduped if _keep(a)]

        return deduped

    def news_by_keyword(self, keyword: str, *, market: str = "CN") -> list[NewsArticle]:
        """按任意关键词(行业/主题词,如"新能源汽车")搜中文新闻,不限股票代码。
        直接复用东财搜索 vendor 的 fetch_by_keyword,单一源、不经聚合/去重。
        market 目前未使用(该 vendor 只支持中文搜索),保留参数位供未来扩展。
        """
        return EastmoneyStockNewsVendor.fetch_by_keyword(keyword)

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
