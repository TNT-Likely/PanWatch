"""TradingAgents v0.5.0 集成扩展点的兼容契约。"""

from __future__ import annotations

import inspect
from importlib.metadata import version

from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.stockstats_utils import load_ohlcv
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph


def test_v050_exposes_the_panwatch_extension_contract():
    """升级或上游重构时，公开持仓入口和既有补丁签名必须仍然可用。"""
    assert version("tradingagents") == "0.5.0"
    assert "portfolio" in inspect.signature(TradingAgentsGraph.propagate).parameters
    assert "portfolio_context" in inspect.signature(Propagator.create_initial_state).parameters
    assert "callbacks" in inspect.signature(Propagator.get_graph_args).parameters
    assert str(inspect.signature(route_to_vendor)) == "(method: str, *args, **kwargs)"
    assert "fill_gaps" in inspect.signature(load_ohlcv).parameters
