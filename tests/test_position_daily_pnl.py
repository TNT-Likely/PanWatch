"""持仓今日盈亏计算。"""

from src.core.position_daily_pnl import TradeLot, compute_position_daily_pnl


def test_same_day_buy_uses_cost_basis():
    """当天新建仓时，今日盈亏应按买入价与现价计算。"""
    daily_pnl, daily_pnl_pct = compute_position_daily_pnl(
        current_price=10.5,
        quantity=100,
        prev_close=9.0,
        today_trades=[TradeLot(side="buy", quantity=100, price=10.0)],
        day_start_qty=0,
    )
    assert daily_pnl == 50.0
    assert daily_pnl_pct == 5.0


def test_overnight_holding_uses_prev_close():
    """无今日流水时，今日盈亏应按昨收与现价计算。"""
    daily_pnl, daily_pnl_pct = compute_position_daily_pnl(
        current_price=11.0,
        quantity=100,
        prev_close=10.0,
        today_trades=[],
        day_start_qty=100,
    )
    assert daily_pnl == 100.0
    assert daily_pnl_pct == 10.0


def test_today_add_on_existing_splits_basis():
    """今日加仓时，应分别按昨收与买入价计算。"""
    daily_pnl, daily_pnl_pct = compute_position_daily_pnl(
        current_price=11.0,
        quantity=150,
        prev_close=10.0,
        today_trades=[TradeLot(side="buy", quantity=50, price=10.5)],
        day_start_qty=100,
    )
    # 隔夜 100 * (11-10) + 今日买入 50 * (11-10.5) = 100 + 25 = 125
    assert daily_pnl == 125.0
    assert daily_pnl_pct is not None
    assert abs(daily_pnl_pct - 125 / (100 * 10 + 50 * 10.5) * 100) < 0.01


def test_today_sell_realizes_intraday_gain():
    """今日卖出应计入已实现今日盈亏。"""
    daily_pnl, _ = compute_position_daily_pnl(
        current_price=11.0,
        quantity=50,
        prev_close=10.0,
        today_trades=[TradeLot(side="sell", quantity=50, price=10.8)],
        day_start_qty=100,
    )
    # 已实现 50*(10.8-10)=40；剩余隔夜 50*(11-10)=50
    assert daily_pnl == 90.0
