"""Qlib positions use explicit cash allocation, never legacy-score tiers."""
from src.modules.qlib_research.contracts import ResearchConfig
from src.modules.qlib_research.costs import QlibCostModel
from src.modules.qlib_research.policy import dropout_plan, lot_quantity
import pytest
from src.modules.paper_trading.paper_trading_engine import _safe_float


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", None, "bad"])
def test_nonfinite_quotes_never_become_execution_inputs(value):
    assert _safe_float(value) is None


def test_budget_cash_and_lot_limits_include_same_qlib_fees():
    costs = QlibCostModel(ResearchConfig().model_dump())
    quantity = lot_quantity(250000, 10, 3000, costs)
    assert quantity % 100 == 0
    assert -costs.fill("buy", 10, quantity).cash_delta <= 3000
    assert lot_quantity(500, 100, 500, costs) == 0
    assert lot_quantity(50, 10, 100000, costs) == 0


def test_dropout_keeps_winners_and_replaces_bottom_ranked_holdings():
    predictions = [{"symbol": "A", "raw_score": -0.1}, {"symbol": "B", "raw_score": -0.2}, {"symbol": "C", "raw_score": -0.3}]
    sell, buy = dropout_plan(predictions, {"A", "C"}, {"A", "B", "C"}, topk=2)
    assert sell == ["C"] and buy == ["B"]
    # The absolute scale and sign of model outputs do not drive position sizing.
    scaled = [{**row, "raw_score": row["raw_score"] * 10000} for row in predictions]
    assert dropout_plan(scaled, {"A", "C"}, {"A", "B", "C"}, 2) == (sell, buy)


def test_untradable_holdings_do_not_force_replacement_beyond_topk():
    predictions = [{"symbol": "A", "raw_score": 3}, {"symbol": "B", "raw_score": 2}, {"symbol": "C", "raw_score": 1}]
    sell, buy = dropout_plan(predictions, {"A", "C"}, {"A", "B"}, 2)
    assert "C" not in sell
    assert len({"A", "C"} - set(sell) | set(buy)) <= 2


def test_aggregate_fees_and_cash_conservation_are_explicit():
    costs = QlibCostModel(ResearchConfig().model_dump())
    assert costs.fill("buy", 10, 100).explicit_fees == 5
    assert costs.fill("sell", 100, 1000).explicit_fees == 80
    assert costs.fill("buy", 100, 1000).slippage_cost == 0
