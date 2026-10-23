"""Integration contracts against the pinned, real TradingAgents v0.7 tools."""
from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode
from langgraph.graph import StateGraph, START, END
from tradingagents.agents import tools
from tradingagents.agents.state import AgentState
from tradingagents.dataflows import router
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors.yahoo import market, ohlcv, snapshot
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph

from src.modules.automation.tradingagents import toolkit_adapter as adapter
from src.modules.automation.tradingagents.decision import map_state_to_result
from src.modules.automation.tradingagents.observability import PanWatchProgressHandler
from src.modules.automation.tradingagents.runtime_support import build_ta_llm_config


def test_v070_exposes_the_panwatch_extension_contract():
    assert version("tradingagents") == "0.7.0"
    assert "portfolio" in inspect.signature(TradingAgentsGraph.propagate).parameters
    assert "portfolio_context" in inspect.signature(Propagator.create_initial_state).parameters
    assert "callbacks" in inspect.signature(Propagator.get_graph_args).parameters
    assert str(inspect.signature(router.route_to_vendor)) == "(method: str, *args, **kwargs)"
    assert "as_of_date" in inspect.signature(ohlcv.load_ohlcv).parameters
    assert "fill_gaps" in inspect.signature(ohlcv.load_ohlcv).parameters
    assert "symbol" not in tools.get_stock_data.tool_call_schema.model_fields
    assert "ticker" not in tools.get_fundamentals.tool_call_schema.model_fields


def _data(symbol, market_code, close):
    return {
        "stock": SimpleNamespace(symbol=symbol, name=f"QA {symbol}", market=SimpleNamespace(value=market_code)),
        "quote": {"current_price": close},
        "klines": [SimpleNamespace(date="2026-10-22", open=close, high=close + 1,
                                  low=close - 1, close=close, volume=1000)],
    }


@pytest.mark.parametrize("symbol,market_code,close", [("600519", "CN", 1500), ("00700", "HK", 500), ("AAPL", "US", 250)])
def test_real_toolnode_binds_the_run_instrument_and_routes_all_markets(monkeypatch, symbol, market_code, close):
    """Spoofed LLM symbols cannot override the InjectedState instrument."""
    data = _data(symbol, market_code, close)
    workflow = StateGraph(AgentState)
    workflow.add_node("tools", ToolNode([tools.get_stock_data]))
    workflow.add_edge(START, "tools")
    workflow.add_edge("tools", END)
    node = workflow.compile()
    upstream_calls = []

    def vendor(method, *args, **kwargs):
        upstream_calls.append((method, args))
        if market_code == "HK":
            raise VendorUnavailableError("controlled Yahoo outage")
        return f"US verified route {args[0]} {close}"

    with adapter.patch_route_to_vendor(), adapter.panwatch_data_context(data):
        monkeypatch.setattr(adapter, "_real_route_to_vendor", vendor)
        result = node.invoke({"company_of_interest": symbol, "trade_date": "2026-10-22", "messages": [AIMessage(content="", tool_calls=[{
            "id": "price", "name": "get_stock_data", "args": {"symbol": "WRONG", "start_date": "2026-10-01", "end_date": "2026-12-31"},
        }])]})
    text = result["messages"][-1].content
    assert symbol in text and str(close) in text and "WRONG" not in text
    if market_code == "CN":
        assert upstream_calls == []
    else:
        assert upstream_calls[0][1][0] == ("0700.HK" if market_code == "HK" else "AAPL")
        assert upstream_calls[0][1][2] == "2026-10-22"


def test_moved_ohlcv_and_snapshot_imports_use_the_same_dated_cache():
    data = _data("600519", "CN", 1500)
    data["klines"].append(SimpleNamespace(date="2026-10-23", open=9999, high=9999, low=9999, close=9999, volume=1))
    with adapter.patch_route_to_vendor(), adapter.panwatch_data_context(data):
        assert market.load_ohlcv is ohlcv.load_ohlcv is snapshot.load_ohlcv
        assert tools.build_verified_market_snapshot is snapshot.build_verified_market_snapshot
        frame = ohlcv.load_ohlcv("600519", as_of_date="2026-10-22", fill_gaps=False)
        text = snapshot.build_verified_market_snapshot("600519", as_of_date="2026-10-22")
    assert frame["Close"].tolist() == [1500]
    assert "1500" in text and "9999" not in text


def test_parallel_tool_contexts_keep_market_and_instrument_identity(monkeypatch):
    def run(symbol, market_code, close):
        with adapter.panwatch_data_context(_data(symbol, market_code, close)):
            return tools.get_stock_data.invoke({"symbol": symbol, "start_date": "2026-10-01", "end_date": "2026-10-22", "trade_date": "2026-10-22"})

    with adapter.patch_route_to_vendor():
        monkeypatch.setattr(adapter, "_real_route_to_vendor", Mock(side_effect=VendorUnavailableError("controlled outage")))
        with ThreadPoolExecutor(max_workers=2) as pool:
            cn = pool.submit(run, "600519", "CN", 1500)
            hk = pool.submit(run, "00700", "HK", 500)
            cn_text, hk_text = cn.result(), hk.result()
    assert "600519" in cn_text and "00700" not in cn_text
    assert "00700" in hk_text and "600519" not in hk_text


@pytest.mark.parametrize("rating,action,review", [("Sell", "sell", False), ("Underweight", "reduce", False), ("REVIEW", "watch", True), ("", "watch", True), ("invalid", "watch", True)])
def test_final_rating_is_authoritative_over_quoted_ratings(rating, action, review):
    state = {"final_rating": rating, "final_trade_decision": "Consensus rating: Buy\nOur rating: Sell", "investment_plan": "Research verdict", "risk_debate_state": {"history": "risk"}, "run_settings": {"version": "0.7.0"}}
    result = map_state_to_result(stock=_data("AAPL", "US", 250)["stock"], ta_result={"decision": "Buy", "final_state": state})
    assert result.raw_data["suggestion"]["action"] == action
    assert result.raw_data["suggestion"]["review_required"] is review
    assert result.raw_data["debate_history"]["judge_decision"] == "Research verdict"
    assert result.raw_data["risk_judgment"] == state["final_trade_decision"]
    assert result.raw_data["run_settings"] == state["run_settings"]


def test_tier_defaults_cannot_override_the_selected_panwatch_service(monkeypatch):
    from tradingagents.default_config import DEFAULT_CONFIG
    monkeypatch.setitem(DEFAULT_CONFIG, "deep_think_provider", "anthropic")
    monkeypatch.setitem(DEFAULT_CONFIG, "quick_think_backend_url", "https://other.example/v1")
    client = SimpleNamespace(base_url="https://selected.example/v1", model="chosen")
    config = build_ta_llm_config(client)
    assert config["deep_think_provider"] == config["quick_think_provider"] == "openrouter"
    assert config["deep_think_backend_url"] == config["quick_think_backend_url"] == client.base_url


def test_parallel_llm_spans_end_with_their_own_usage_and_errors(monkeypatch):
    from src.modules.automation.tradingagents import observability as obs
    start = Mock(side_effect=["span-a", "span-b", "span-c"])
    end, attrs = Mock(), Mock()
    monkeypatch.setattr(obs.otel, "start_detached_span", start)
    monkeypatch.setattr(obs.otel, "end_span", end)
    monkeypatch.setattr(obs.otel, "set_span_attributes", attrs)
    handler = PanWatchProgressHandler("qa-parallel")
    monkeypatch.setattr(handler, "_emit", Mock())
    handler.on_llm_start({}, [], run_id="a")
    handler.on_llm_start({}, [], run_id="b")
    handler.on_llm_end(SimpleNamespace(llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 2}}), run_id="a")
    handler.on_llm_error(RuntimeError("controlled failure"), run_id="b")
    handler.on_llm_start({}, [], run_id="c")
    handler.on_llm_end(SimpleNamespace(llm_output={"token_usage": {"prompt_tokens": 20, "completion_tokens": 4}}), run_id="c")
    assert [c.args[0] for c in end.call_args_list] == ["span-a", "span-b", "span-c"]
    assert [c.args[0] for c in attrs.call_args_list] == ["span-a", "span-c"]
    assert handler.token_usage["total_tokens"] == 36
    assert not handler._otel_llm_spans and not handler._llm_runs
