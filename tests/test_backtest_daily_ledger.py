from dataclasses import replace

import pytest

from src.modules.strategy.backtest.engine import Backtester, Signal
from src.modules.strategy.backtest.cost_model import CostConfig, CostModel
from src.modules.strategy.backtest.data_adapter import PriceBar


def engine(**kwargs):
    return Backtester(CostModel(CostConfig(commission_rate=0, min_commission=0, stamp_duty_rate=0,
        transfer_fee_rate=0, slippage_bps=0)), initial_capital=1000, cash_per_trade=500, lot=1, **kwargs)


def history(prices):
    return [PriceBar(f"2026-01-{i + 1:02}", price, price, price, price, 100) for i, price in enumerate(prices)]


def test_daily_marks_capture_unrealized_drawdown_and_reconcile_final_cash():
    result = engine().run([Signal("X", "CN", "2026-01-01")], {"X": history([10, 10, 5, 10])})
    assert result.equity_curve == [1000, 1000, 750, 1000]
    assert result.metrics["max_drawdown"] == .25
    assert len(set(result.equity_dates)) == len(result.equity_curve)
    assert result.equity_curve[-1] == 1000 + sum(trade.pnl for trade in result.trades)


def test_simultaneous_positions_reserve_cash_and_cannot_double_spend():
    result = engine().run([Signal(symbol, "CN", "2026-01-01") for symbol in ["X", "Y", "Z"]],
                          {symbol: history([10, 10, 11]) for symbol in ["X", "Y", "Z"]})
    assert len(result.trades) == 2 and result.skipped == 1
    assert result.equity_curve[-1] == 1100


def test_annualization_uses_trading_periods_instead_of_number_of_exits():
    short = engine().run([Signal("X", "CN", "2026-01-01")], {"X": history([10, 10, 11])})
    long = engine().run([Signal("X", "CN", "2026-01-01")], {"X": history([10, 10, 10, 10, 11])})
    assert short.metrics["total_return"] == long.metrics["total_return"]
    assert short.metrics["annualized_return"] > long.metrics["annualized_return"]


def test_limit_up_prevents_entry_and_limit_down_keeps_unsellable_position_open():
    bars = history([10, 10, 9])
    locked_entry = [bars[0], replace(bars[1], limit_up=10), bars[2]]
    assert engine().run([Signal("X", "CN", bars[0].date)], {"X": locked_entry}).skipped == 1
    locked_exit = [*bars[:2], replace(bars[2], limit_down=9)]
    result = engine().run([Signal("X", "CN", bars[0].date)], {"X": locked_exit})
    assert result.trades == [] and len(result.open_positions) == 1
    assert result.equity_curve[-1] == 950


def test_entry_on_last_bar_is_not_forcibly_sold_in_violation_of_t1():
    result = engine().run([Signal("X", "CN", "2026-01-01")], {"X": history([10, 10])})
    assert not result.trades and len(result.open_positions) == 1
