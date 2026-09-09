"""TradingAgents 历史对比模块单测。"""

from __future__ import annotations

from src.modules.automation.tradingagents.history_comparison import (
    _classify_hit,
    _compute_stats,
    _find_close_after_n_trading_days,
    _find_close_on_or_after,
)


def test_classify_hit_buy_up_hits():
    """buy 决策后续涨 → hit=True"""
    assert _classify_hit("buy", 5.0) is True


def test_classify_hit_buy_down_misses():
    """buy 决策后续跌 → hit=False"""
    assert _classify_hit("buy", -3.0) is False


def test_classify_hit_sell_down_hits():
    """sell 决策后续跌 → hit=True"""
    assert _classify_hit("sell", -2.5) is True


def test_classify_hit_hold_flat_hits():
    """hold 决策横盘(|x|<2%) → hit=True"""
    assert _classify_hit("hold", 1.5) is True
    assert _classify_hit("hold", -1.0) is True


def test_classify_hit_hold_big_move_misses():
    """hold 决策大幅波动 → hit=False"""
    assert _classify_hit("hold", 5.0) is False


def test_classify_hit_unknown_action_returns_none():
    """未知 action 返回 None"""
    assert _classify_hit("xyz", 5.0) is None


def test_classify_hit_no_data_returns_none():
    """无后续收益数据 → None"""
    assert _classify_hit("buy", None) is None


def test_find_close_on_or_after_exact_match():
    """目标日就是交易日 → 直接返回"""
    klines = {"2026-05-15": 10.5, "2026-05-16": 10.7}
    result = _find_close_on_or_after(klines, "2026-05-15")
    assert result == ("2026-05-15", 10.5)


def test_find_close_on_or_after_weekend_skips_to_monday():
    """目标日是周末/节假日 → 找下一个交易日"""
    klines = {"2026-05-15": 10.5, "2026-05-18": 11.0}  # 16/17 是周末
    result = _find_close_on_or_after(klines, "2026-05-16")
    assert result == ("2026-05-18", 11.0)


def test_find_close_on_or_after_no_match_returns_none():
    """7 天内没找到任何交易日 → None"""
    klines = {"2026-01-01": 10.0}
    result = _find_close_on_or_after(klines, "2026-05-16")
    assert result is None


def test_find_close_after_n_trading_days():
    """从基准日往后 N 个交易日找收盘价"""
    sorted_dates = ["2026-05-15", "2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21"]
    klines = {d: 10.0 + i for i, d in enumerate(sorted_dates)}
    # 从 5-15 往后 3 个交易日 = 5-20 → close=13.0
    assert _find_close_after_n_trading_days(sorted_dates, "2026-05-15", 3, klines) == 13.0


def test_find_close_after_n_trading_days_overflow_returns_none():
    """N 超过可用区间 → None"""
    sorted_dates = ["2026-05-15", "2026-05-18"]
    klines = {"2026-05-15": 10.0, "2026-05-18": 11.0}
    assert _find_close_after_n_trading_days(sorted_dates, "2026-05-15", 10, klines) is None


def test_compute_stats_empty_returns_zero_total():
    """空列表 → total=0"""
    assert _compute_stats([])["total"] == 0


def test_compute_stats_hit_rates_per_action():
    """按 action 分别统计 hit rate"""
    items = [
        {"action": "buy", "return_20d_pct": 5.0, "hit_20d": True},
        {"action": "buy", "return_20d_pct": -2.0, "hit_20d": False},
        {"action": "sell", "return_20d_pct": -3.0, "hit_20d": True},
        {"action": "hold", "return_20d_pct": 0.5, "hit_20d": True},
    ]
    stats = _compute_stats(items)
    assert stats["total"] == 4
    assert stats["buy_count"] == 2
    assert stats["sell_count"] == 1
    assert stats["hold_count"] == 1
    assert stats["buy_hit_rate"] == 0.5
    assert stats["sell_hit_rate"] == 1.0
    assert stats["hold_hit_rate"] == 1.0
    assert stats["overall_hit_rate"] == 0.75
    # (5-2-3+0.5)/4 = 0.125,Python round() 银行家舍入到偶数 → 0.12
    assert stats["avg_return_20d_pct"] == 0.12


def test_compute_stats_skips_items_without_20d_return():
    """未到 20 天的最新决策不参与统计"""
    items = [
        {"action": "buy", "return_20d_pct": None, "hit_20d": None},  # 刚发生
        {"action": "buy", "return_20d_pct": 5.0, "hit_20d": True},
    ]
    stats = _compute_stats(items)
    assert stats["total"] == 2
    assert stats["buy_hit_rate"] == 1.0  # 仅基于第 2 条
