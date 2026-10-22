"""Real upstream tool boundary: numeric CN codes retain exchange identity."""
from importlib import import_module
from types import SimpleNamespace

import pytest

from src.modules.automation.tradingagents import toolkit_adapter as ta


def _upstream(legacy, modern):
    try:
        return import_module(legacy)
    except ModuleNotFoundError as exc:
        if exc.name != legacy and not legacy.startswith(f"{exc.name}."):
            raise
        return import_module(modern)

@pytest.mark.parametrize("requested", ["000001.SS", "000001.BJ", "600519.SZ", "920001.SS"])
def test_wrong_cn_exchange_never_reuses_bare_stock_snapshot(monkeypatch, requested):
    stock = SimpleNamespace(symbol=requested.split('.')[0], market="CN", name="Cached stock")
    tools = _upstream("tradingagents.agents.utils.core_stock_tools", "tradingagents.agents.tools")
    monkeypatch.setattr(ta, "_emit_toolkit_log", lambda *a, **k: None)
    upstream_calls = []
    def upstream(method, symbol, *args, **kwargs):
        upstream_calls.append(symbol)
        return "EXACT_UPSTREAM_LISTING"
    monkeypatch.setattr(_upstream("tradingagents.dataflows.interface", "tradingagents.dataflows.router"), "route_to_vendor", upstream)
    with ta.panwatch_data_context({"stock": stock, "klines": [dict(date="2026-10-09", open=10, high=11, low=9, close=10, volume=100)]}), ta.patch_route_to_vendor():
        assert not ta._snapshot_matches(requested, "CN")
        result = tools.get_stock_data.invoke({"symbol": requested, "start_date": "2026-10-01", "end_date": "2026-10-09"})
        # Legacy routing may preserve the exact upstream symbol; modern CN
        # interception must fail closed rather than serve another listing.
        if ta.is_a_share(requested):
            assert "DATA_UNAVAILABLE" in result
            assert upstream_calls == []
        else:
            assert "EXACT_UPSTREAM_LISTING" in result
            assert upstream_calls == [requested]
        assert "2026-10-09,10,11,9,10,100" not in result


@pytest.mark.parametrize("requested", ["000001.SS", "000001.BJ", "600519.SZ", "920001.SS"])
def test_wrong_cn_exchange_never_reaches_bare_collector(monkeypatch, requested):
    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    from src.platform.marketdata.models import MarketCode
    monkeypatch.setattr(ta, "_market_for_symbol", lambda _: MarketCode.CN)
    monkeypatch.setattr(KlineCollector, "get_klines", lambda *a, **k: pytest.fail("wrong exchange must not reach stock collector"))
    with ta.panwatch_data_context({}):
        with pytest.raises(ValueError, match="No verified PanWatch listing"):
            ta._build_panwatch_ohlcv_df(requested, "2026-10-09")


@pytest.mark.parametrize("requested,bare", [("000001.SZ", "000001"), ("600519.SS", "600519"), ("920001.BJ", "920001")])
def test_matching_cn_exchange_keeps_snapshot_alias(requested, bare):
    with ta.panwatch_data_context({"stock": SimpleNamespace(symbol=bare, market="CN")}):
        assert ta._snapshot_matches(requested, "CN")
        assert ta._panwatch_identity(requested, "CN")[1] == bare
