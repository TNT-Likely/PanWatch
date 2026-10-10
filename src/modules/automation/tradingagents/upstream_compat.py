"""Feature-detected TradingAgents import boundaries: official 0.5 and fork 0.6."""
from importlib import import_module
from types import ModuleType


def upstream_module(legacy: str, modern: str) -> ModuleType:
    """Do not mask missing dependencies inside an otherwise present module."""
    try:
        return import_module(legacy)
    except ModuleNotFoundError as exc:
        if exc.name and not (legacy == exc.name or legacy.startswith(exc.name + ".")):
            raise
        return import_module(modern)


def vendor_router() -> ModuleType:
    return upstream_module("tradingagents.dataflows.interface", "tradingagents.dataflows.router")


def ohlcv_module() -> ModuleType:
    return upstream_module("tradingagents.dataflows.stockstats_utils", "tradingagents.dataflows.vendors.yahoo.ohlcv")


def snapshot_module() -> ModuleType:
    return upstream_module("tradingagents.dataflows.market_data_validator", "tradingagents.dataflows.vendors.yahoo.snapshot")


def validate_upstream_contract() -> None:
    """Reject incompatible custom builds before starting an analysis."""
    from inspect import signature
    from tradingagents.graph.propagation import Propagator
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    checks = (
        (vendor_router(), "route_to_vendor", ("get_stock_data", "SYMBOL"), {"vendor": "yfinance"}),
        (ohlcv_module(), "load_ohlcv", ("SYMBOL", "2026-01-01"), {"fill_gaps": False}),
        (snapshot_module(), "build_verified_market_snapshot", ("SYMBOL", "2026-01-01"), {"indicators": None}),
    )
    for owner, name, args, kwargs in checks:
        try:
            signature(getattr(owner, name)).bind(*args, **kwargs)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported TradingAgents API: {name}; install a compatible source (see docs/tradingagents-source.md)") from exc
    for owner, name, parameter in (
        (TradingAgentsGraph, "propagate", "portfolio"),
        (Propagator, "create_initial_state", "portfolio_context"),
        (Propagator, "get_graph_args", "callbacks"),
    ):
        try:
            if parameter not in signature(getattr(owner, name)).parameters:
                raise TypeError("missing required parameter")
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported TradingAgents API: {name}; install a compatible source (see docs/tradingagents-source.md)") from exc
