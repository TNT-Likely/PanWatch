"""Compatibility package for the automation TradingAgents implementation."""

import importlib
import sys

from src.modules.automation import tradingagents as _implementation

for _name in (
    "agent", "auto_trigger", "backfill", "cost_tracker", "financial_data",
    "history_comparison", "langchain_compat", "llm_adapter", "paper_trading_bridge",
    "portfolio_context", "progress", "result_mapper", "toolkit_adapter",
):
    _module = importlib.import_module(f"src.modules.automation.tradingagents.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    setattr(_implementation, _name, _module)

sys.modules[__name__] = _implementation
