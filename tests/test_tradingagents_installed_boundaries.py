"""End-to-end installed-source adapter regressions. No SDK/network requests."""
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier, Event
from types import MethodType, SimpleNamespace

import pytest

from src.modules.automation.tradingagents import toolkit_adapter as ta
from src.modules.automation.tradingagents import upstream_compat as compat
from src.modules.automation.tradingagents.agent import _bounded_graph_class
from src.modules.automation.tradingagents.runtime_support import build_ta_llm_config
from tradingagents.graph import trading_graph as tg


def config(tmp_path, timeout=7):
    return build_ta_llm_config(SimpleNamespace(model=f"model-{timeout}", base_url="https://example.invalid/v1"),
                               runtime_dir=str(tmp_path), llm_timeout_seconds=timeout)


def graph_without_clients(cfg, runner):
    graph = tg.TradingAgentsGraph.__new__(tg.TradingAgentsGraph)
    graph.config = cfg
    graph._checkpointer_ctx = None
    graph._run_graph = MethodType(runner, graph)
    return graph


@pytest.mark.parametrize("symbol,exchange", [("600519", "SS"), ("000001", "SZ"), ("920001", "BJ")])
def test_actual_graph_canonical_cn_reuses_snapshot(monkeypatch, tmp_path, symbol, exchange):
    modern = not hasattr(tg.TradingAgentsGraph, "_get_provider_kwargs")
    if modern:
        identity = import_module("tradingagents.extensions.ashare_identity")
        def lookup(request_exchange, code, *args):
            if request_exchange == exchange:
                return {"exchange": {"SS": "SSE", "SZ": "SZSE", "BJ": "BSE"}[exchange],
                        "canonical_symbol": code + "." + exchange, "chinese_short_name": "测试公司",
                        "source": {"provider": exchange, "url": "https://example.invalid/"}}
        monkeypatch.setattr(identity, "_lookup", lookup)
        monkeypatch.setattr(tg, "create_run", lambda *a, **k: None)
    tools = compat.upstream_module("tradingagents.agents.utils.core_stock_tools", "tradingagents.agents.tools")
    monkeypatch.setattr(compat.vendor_router(), "route_to_vendor", lambda *a, **k: pytest.fail("must use cached CN data"))
    monkeypatch.setattr(ta, "_emit_toolkit_log", lambda *a, **k: None)
    seen = []
    def run(self, ticker, trade_date, **kwargs):
        seen.append(ticker)
        result = tools.get_stock_data.invoke({"symbol": ticker, "start_date": "2026-10-01", "end_date": trade_date})
        assert "2026-10-09,10,11,9,10,100" in result
        return {}, "TEST"
    graph = graph_without_clients(config(tmp_path), run)
    stock = SimpleNamespace(symbol=symbol, market=SimpleNamespace(value="CN"), name="测试公司")
    with ta.panwatch_data_context({"stock": stock, "klines": [dict(date="2026-10-09", open=10, high=11, low=9, close=10, volume=100)]}), ta.patch_route_to_vendor():
        graph.propagate(symbol, "2026-10-09")
    assert seen == [symbol + "." + exchange if modern else symbol]


@pytest.mark.parametrize("symbol,market,cached,expected", [
    ("600519.SS", "CN", "600519", True), ("000001.SZ", "CN", "000001", True),
    ("920001.BJ", "CN", "920001", True), ("600519.SS", "US", "600519", False),
    ("600519.SS", "HK", "600519", False), ("600519.SS", "CN", "600520", False),
    ("600519.HK", "CN", "600519", False), ("BRK.B", "CN", "BRK", False),
    ("FOO.SS", "CN", "FOO", False),
])
def test_cache_identity_requires_same_market(symbol, market, cached, expected):
    with ta.panwatch_data_context({"stock": SimpleNamespace(symbol=cached, market=SimpleNamespace(value=market))}):
        assert ta._snapshot_matches(symbol) is expected


@pytest.mark.parametrize("symbol,bare", [("600519.SS", "600519"), ("000001.SZ", "000001"), ("920001.BJ", "920001")])
def test_cn_ohlcv_collector_receives_panwatch_form(monkeypatch, symbol, bare):
    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    calls = []
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, ticker, **kw: calls.append((self.market, ticker)) or [])
    with ta.panwatch_data_context({}):
        assert ta._build_panwatch_ohlcv_df(symbol, "2026-10-09") is None
    assert calls[0][0].value == "CN" and calls[0][1] == bare


def test_actual_client_factories_forward_concurrent_timeouts(monkeypatch, tmp_path):
    modern = not hasattr(tg.TradingAgentsGraph, "_get_provider_kwargs")
    factory = import_module("tradingagents.llm_clients.factory") if modern else tg
    original_init = tg.TradingAgentsGraph.__init__
    original_tier = getattr(tg, "create_tier_client", None)
    original_builder = getattr(factory, "build_llm_kwargs", None)
    barrier = Barrier(2)
    captured = []
    counts = {}
    class StopProbe(Exception):
        pass
    def capture(**kwargs):
        model = kwargs["model"]
        counts[model] = counts.get(model, 0) + 1
        captured.append(kwargs)
        if counts[model] == 1:
            barrier.wait(timeout=10)
            return SimpleNamespace(get_llm=lambda: object())
        raise StopProbe()
    monkeypatch.setattr(factory, "create_llm_client", capture)
    bounded = _bounded_graph_class(tg.TradingAgentsGraph)
    configs = [config(tmp_path / str(timeout), timeout) for timeout in (7, 19)]
    def construct(cfg):
        with pytest.raises(StopProbe):
            bounded(config=cfg)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(construct, configs))
    assert len(captured) == 4
    for kwargs in captured:
        assert kwargs["timeout"] == float(kwargs["model"].split("-")[-1])
        assert kwargs["max_retries"] == 0
    assert tg.TradingAgentsGraph.__init__ is original_init
    assert getattr(tg, "create_tier_client", None) is original_tier
    assert getattr(factory, "build_llm_kwargs", None) is original_builder
    assert [cfg["llm_timeout_seconds"] for cfg in configs] == [7, 19]


def test_unsupported_timeout_boundary_fails_clear():
    class UnknownGraph:
        def __init__(self, config=None):
            pass
    with pytest.raises(ValueError, match="LLM timeout boundary"):
        _bounded_graph_class(UnknownGraph)


@pytest.mark.parametrize("tool_name", ["get_news", "get_global_news"])
@pytest.mark.parametrize("when", ["before", "during_return", "during_raise"])
def test_actual_public_news_tools_stop_before_enrichment(monkeypatch, tmp_path, tool_name, when):
    news = compat.upstream_module("tradingagents.agents.utils.news_data_tools", "tradingagents.agents.tools")
    event = Event()
    primary_calls = []
    if when == "before":
        event.set()
    def primary(*a, **kw):
        primary_calls.append(a)
        event.set()
        if when == "during_raise":
            raise RuntimeError("primary interrupted")
        return "DATA_UNAVAILABLE: primary has no news"
    monkeypatch.setattr(compat.vendor_router(), "route_to_vendor", primary)
    if not hasattr(tg.TradingAgentsGraph, "_get_provider_kwargs"):
        for path, attr in [("duckduckgo_news", "fetch_news"), ("company_website", "resolve_company_website"), ("ashare_announcements", "fetch_announcements")]:
            monkeypatch.setattr(import_module("tradingagents.extensions." + path), attr,
                                lambda *a, **k: pytest.fail("extra source request after cancellation"))
        monkeypatch.setattr(tg, "create_run", lambda *a, **k: None)
    def run(self, symbol, trade_date, **kwargs):
        values = {"curr_date": trade_date} if tool_name == "get_global_news" else {"ticker": symbol, "start_date": "2026-10-01", "end_date": trade_date}
        getattr(news, tool_name).invoke(values)
        pytest.fail("cancellation must escape graph")
    graph = graph_without_clients(config(tmp_path), run)
    with ta.panwatch_data_context({}, cancel_event=event), ta.patch_route_to_vendor():
        with pytest.raises(ta.TradingAgentsCancelled):
            graph.propagate("AAPL", "2026-10-09")
    assert len(primary_calls) == (0 if when == "before" else 1)
    assert ta._patch_refcount == 0


def test_actual_news_cancellation_contexts_are_isolated(monkeypatch):
    news = compat.upstream_module("tradingagents.agents.utils.news_data_tools", "tradingagents.agents.tools")
    router = compat.vendor_router()
    def primary(*a, **kw):
        return "Verified primary news content with an unchanged ordinary vendor format."
    monkeypatch.setattr(router, "route_to_vendor", primary)
    original_news = getattr(news, "retrieve_news", None)
    barrier = Barrier(2)
    def run(cancel):
        event = Event()
        if cancel:
            event.set()
        with ta.panwatch_data_context({}, cancel_event=event), ta.patch_route_to_vendor():
            barrier.wait(timeout=10)
            if cancel:
                with pytest.raises(ta.TradingAgentsCancelled):
                    news.get_global_news.invoke({"curr_date": "2026-10-09"})
                return "cancelled"
            return news.get_global_news.invoke({"curr_date": "2026-10-09"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [True, False]))
    assert results == ["cancelled", primary()]
    assert router.route_to_vendor is primary
    assert getattr(news, "retrieve_news", None) is original_news
    assert ta._patch_refcount == 0


def test_actual_modern_news_stops_between_enrichment_sources(monkeypatch):
    if hasattr(tg.TradingAgentsGraph, "_get_provider_kwargs"):
        pytest.skip("official legacy has no enrichment wrapper")
    news = import_module("tradingagents.agents.tools")
    evidence = import_module("tradingagents.extensions.news_evidence")
    ddg = import_module("tradingagents.extensions.duckduckgo_news")
    official = import_module("tradingagents.extensions.ashare_announcements")
    website = import_module("tradingagents.extensions.company_website")
    event = Event()
    monkeypatch.setattr(compat.vendor_router(), "route_to_vendor", lambda *a, **k: "DATA_UNAVAILABLE: no primary")
    monkeypatch.setattr(evidence, "get_config", lambda: {"duckduckgo_news_enabled": True, "ashare_announcements_enabled": True})
    monkeypatch.setattr(evidence, "identity_for", lambda *a: dict(status="resolved", security_type="A-share", confidence="verified"))
    monkeypatch.setattr(website, "resolve_company_website", lambda *a: None)
    calls = []
    def fetch(*a, **kw):
        calls.append("news")
        event.set()
        return {"evidence": [], "diagnostics": {}}
    monkeypatch.setattr(ddg, "fetch_news", fetch)
    monkeypatch.setattr(official, "fetch_announcements", lambda *a, **k: pytest.fail("cancelled before second enrichment source"))
    with ta.panwatch_data_context({}, cancel_event=event), ta.patch_route_to_vendor():
        with pytest.raises(ta.TradingAgentsCancelled):
            news.get_news.invoke({"ticker": "600519.SS", "start_date": "2026-10-01", "end_date": "2026-10-09"})
    assert calls == ["news"]


def test_cached_cn_alias_ohlcv_and_wrong_market_separation(monkeypatch):
    from src.platform.marketdata.collectors.kline_collector import KlineCollector, KlineData
    cached = [KlineData(date="2026-10-09", open=10, high=11, low=9, close=10, volume=100)]
    calls = []
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, ticker, **kw: calls.append((self.market.value, ticker)) or [])
    for market in ("CN", "HK", "US"):
        with ta.panwatch_data_context({"stock": SimpleNamespace(symbol="600519", market=market), "klines": cached}):
            df = ta._build_panwatch_ohlcv_df("600519.SS", "2026-10-09")
            assert (df is not None) == (market == "CN")
    assert calls == [("CN", "600519"), ("CN", "600519")]
