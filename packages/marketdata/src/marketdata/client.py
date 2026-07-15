"""对象式入口:注入 ConfigProvider(+可选 MetricsSink),对外提供 quotes()/health()。"""

from __future__ import annotations

from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.ports import ConfigProvider, MetricsSink
from marketdata.symbol import Symbol
from marketdata.types import Quote, Request
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

    def health(self) -> dict[str, dict]:
        """每个 vendor 的内存健康度快照(成功率 / p50 延迟 / 最近错误)。"""
        return self.metrics.snapshot()
