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
