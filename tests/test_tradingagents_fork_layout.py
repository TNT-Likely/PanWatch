"""Installed legacy/modern layouts must share the same narrow adapter boundary."""
from importlib import import_module
from importlib.metadata import version
import threading
from types import SimpleNamespace

import pytest
import tradingagents

from src.modules.automation.tradingagents import toolkit_adapter as ta
from src.modules.automation.tradingagents import upstream_compat as compat


def test_installed_distribution_matches_imported_version():
    installed = version("tradingagents")
    assert installed
    if hasattr(tradingagents, "__version__"):
        assert installed == tradingagents.__version__


def test_import_boundary_prefers_legacy(monkeypatch):
    calls = []
    sentinel = object()
    monkeypatch.setattr(compat, "import_module", lambda name: calls.append(name) or sentinel)
    assert compat.upstream_module("old.module", "new.module") is sentinel
    assert calls == ["old.module"]


@pytest.mark.parametrize("missing", ["old", "old.module"])
def test_import_boundary_falls_back_only_for_missing_layout(monkeypatch, missing):
    sentinel = object()
    def load(name):
        if name == "old.module":
            raise ModuleNotFoundError("missing layout", name=missing)
        assert name == "new.module"
        return sentinel
    monkeypatch.setattr(compat, "import_module", load)
    assert compat.upstream_module("old.module", "new.module") is sentinel


def test_import_boundary_does_not_hide_missing_dependency(monkeypatch):
    def load(name):
        raise ModuleNotFoundError("missing dependency", name="dependency")
    monkeypatch.setattr(compat, "import_module", load)
    with pytest.raises(ModuleNotFoundError, match="missing dependency"):
        compat.upstream_module("old.module", "new.module")


@pytest.fixture
def installed_hooks(monkeypatch):
    """Restore even permanent hook globals/import bindings after each probe."""
    router = compat.vendor_router()
    ohlcv = compat.ohlcv_module()
    snapshot = compat.snapshot_module()
    sites = {router, ohlcv, snapshot}
    for path in (*ta._ROUTE_TO_VENDOR_IMPORT_SITES, *ta._LOAD_OHLCV_IMPORT_SITES,
                 *ta._MARKET_SNAPSHOT_IMPORT_SITES):
        try:
            sites.add(import_module(path))
        except ModuleNotFoundError:
            pass
    for mod in sites:
        for attr in ("route_to_vendor", "load_ohlcv", "build_verified_market_snapshot"):
            if hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, getattr(mod, attr))
    for attr in ("_LOAD_OHLCV_PATCHED", "_MARKET_SNAPSHOT_PATCHED", "_real_load_ohlcv",
                 "_real_build_verified_market_snapshot", "_real_route_to_vendor"):
        monkeypatch.setattr(ta, attr, getattr(ta, attr))
    monkeypatch.setattr(ta, "_emit_toolkit_log", lambda *a, **kw: None)
    return router, ohlcv, snapshot, sites


def test_installed_import_sites_are_patched_and_nested_router_restored(installed_hooks):
    router, ohlcv, snapshot, sites = installed_hooks
    original = router.route_to_vendor
    with ta.patch_route_to_vendor():
        with ta.patch_route_to_vendor():
            assert router.route_to_vendor is ta._patched_route_to_vendor
            for mod in sites:
                if hasattr(mod, "route_to_vendor"):
                    assert mod.route_to_vendor is ta._patched_route_to_vendor
                if hasattr(mod, "load_ohlcv"):
                    assert mod.load_ohlcv is ta._panwatch_load_ohlcv
                if hasattr(mod, "build_verified_market_snapshot"):
                    assert mod.build_verified_market_snapshot is ta._safe_build_verified_market_snapshot
        assert router.route_to_vendor is ta._patched_route_to_vendor
    assert router.route_to_vendor is original
    assert ta._patch_refcount == 0


@pytest.mark.parametrize("symbol,market,routed", [("600519", "CN", None),
    ("00700", "HK", "0700.HK"), ("AAPL", "US", "AAPL")])
def test_installed_tool_routes_original_markets(installed_hooks, monkeypatch, symbol, market, routed):
    router, _, _, _ = installed_hooks
    calls = []
    def upstream(method, *args, **kwargs):
        calls.append((method, args, kwargs))
        return "Verified upstream stock data with sufficient content for vendor success."
    monkeypatch.setattr(router, "route_to_vendor", upstream)
    tools = compat.upstream_module("tradingagents.agents.utils.core_stock_tools", "tradingagents.agents.tools")
    stock = SimpleNamespace(symbol=symbol, name="Exact company", market=SimpleNamespace(value=market))
    data = {"stock": stock, "quote": {}, "klines": [
        {"date": "2026-10-09", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}]}
    with ta.panwatch_data_context(data), ta.patch_route_to_vendor():
        result = tools.get_stock_data.invoke({"symbol": symbol, "start_date": "2026-10-01", "end_date": "2026-10-09"})
    if routed is None:
        assert not calls
        assert "2026-10-09" in result
    else:
        assert calls[0][1][0] == routed


def test_installed_tools_respect_cancellation(installed_hooks):
    router, ohlcv, snapshot, _ = installed_hooks
    event = threading.Event()
    event.set()
    with ta.panwatch_data_context({}, cancel_event=event), ta.patch_route_to_vendor():
        for call in (lambda: router.route_to_vendor("get_stock_data", "AAPL", "2026-10-01", "2026-10-09"),
                     lambda: ohlcv.load_ohlcv("AAPL", "2026-10-09"),
                     lambda: snapshot.build_verified_market_snapshot("AAPL", "2026-10-09")):
            with pytest.raises(ta.TradingAgentsCancelled):
                call()
