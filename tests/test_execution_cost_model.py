"Legacy fee accounting retained for existing manual paper exits."

from src.platform.execution.cost_model import CostModel

def test_cost_model_stamp_duty_sell_only():
    """印花税仅卖出单边收取,买入不收。"""
    cm = CostModel()
    assert cm.fill("buy", 10.0, 1000).stamp_duty == 0.0
    assert cm.fill("sell", 10.0, 1000).stamp_duty > 0.0


def test_cost_model_min_commission():
    """小额成交佣金不低于最低 5 元。"""
    f = CostModel().fill("buy", 5.0, 100)  # gross≈500,万2.5≈0.125 → 取 5
    assert f.commission == 5.0


def test_round_trip_pnl_deducts_cost():
    """同价买卖应净亏损(被成本与滑点吃掉)。"""
    rt = CostModel().round_trip_pnl(10.0, 10.0, 1000)
    assert rt["pnl"] < 0
    assert rt["total_cost"] > 0


# ──────────────── 绩效指标 ────────────────
