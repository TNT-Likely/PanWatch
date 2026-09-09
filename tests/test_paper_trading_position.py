"""模拟盘仓位管理(Phase 1)单元测试 —— 纯函数,不触发 DB/网络。"""

from src.modules.strategy.backtest.cost_model import CostModel
from src.modules.paper_trading.paper_trading_engine import _compute_quantity, _position_weight


def test_position_weight_tiers():
    """信号强度越高,单笔资金占比越大(分档)。"""
    assert _position_weight(90) == 0.25
    assert _position_weight(80) == 0.18
    assert _position_weight(70) == 0.12
    assert _position_weight(50) == 0.08
    assert _position_weight(90) > _position_weight(50)


def test_compute_quantity_respects_budget():
    """按市场预算 × 强度比例分配,买入 100 股整数倍且不超预算对应股数。"""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=10.0,
        available_cash=1_000_000, cost_model=cm,
    )
    assert qty > 0 and qty % 100 == 0
    assert qty <= 25000  # 25% 预算 / 10 元


def test_compute_quantity_respects_cash():
    """可用现金不足时回退到买得起的手数,买入含费不超现金。"""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=10.0,
        available_cash=3000, cost_model=cm,
    )
    assert qty % 100 == 0
    if qty > 0:
        outlay = -cm.fill("buy", 10.0, qty).cash_delta
        assert outlay <= 3000


def test_compute_quantity_insufficient_cash_returns_zero():
    """现金连最小一手都买不起时返回 0(应跳过建仓)。"""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=100.0,
        available_cash=500, cost_model=cm,
    )
    assert qty == 0


def test_engine_imports_ok():
    """改造后 paper_trading_engine 可正常导入(无语法/循环 import 错),关键符号在位。"""
    import src.modules.paper_trading.paper_trading_engine as e

    assert hasattr(e, "ENGINE")
    assert hasattr(e, "COST_MODEL")
    assert hasattr(e, "_compute_quantity")
