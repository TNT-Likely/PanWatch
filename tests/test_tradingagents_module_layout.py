"""TradingAgents 适配目录的模块边界契约。"""

from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).parents[1] / "src/modules/automation/tradingagents"


def test_tradingagents_adapter_has_compact_target_modules():
    expected = {
        "agent.py",
        "toolkit_adapter.py",
        "data_context.py",
        "runtime_support.py",
        "observability.py",
        "decision.py",
        "operations.py",
        "__init__.py",
    }

    assert {path.name for path in PACKAGE_DIR.glob("*.py")} == expected


def test_tradingagents_package_exports_stable_runtime_entries():
    from src.modules.automation.tradingagents import (
        PanWatchProgressHandler,
        TradingAgentsAgent,
        aggregate_progress,
        build_stock_metadata_context,
        map_state_to_result,
        patch_instrument_context,
        to_tradingagents_portfolio,
    )

    assert TradingAgentsAgent is not None
    assert PanWatchProgressHandler is not None
    assert aggregate_progress is not None
    assert build_stock_metadata_context is not None
    assert map_state_to_result is not None
    assert patch_instrument_context is not None
    assert to_tradingagents_portfolio is not None
