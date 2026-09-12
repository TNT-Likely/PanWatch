"""回测绩效指标 —— 纯函数,仅依赖标准库。

约定:
- equity_curve: list[float],逐(交易日)净值序列(含浮动盈亏),首元素为期初资金。
- trade_pnls: list[float],每笔已平仓交易的净盈亏(已扣成本)。
"""

from __future__ import annotations

import math
import statistics

TRADING_DAYS_PER_YEAR = 252


def daily_returns(equity_curve: list[float]) -> list[float]:
    """由净值序列推日收益率。"""
    out: list[float] = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev and prev > 0:
            out.append(equity_curve[i] / prev - 1.0)
        else:
            out.append(0.0)
    return out


def total_return(equity_curve: list[float]) -> float:
    if len(equity_curve) < 2 or not equity_curve[0]:
        return 0.0
    return equity_curve[-1] / equity_curve[0] - 1.0


def annualized_return(
    equity_curve: list[float], periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    n = len(equity_curve) - 1
    if n <= 0 or not equity_curve[0] or equity_curve[0] <= 0 or equity_curve[-1] <= 0:
        return 0.0
    growth = equity_curve[-1] / equity_curve[0]
    return growth ** (periods_per_year / n) - 1.0


def max_drawdown(equity_curve: list[float]) -> float:
    """最大回撤(正数,如 0.23 表示 -23%)。"""
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    mdd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    return mdd


def sharpe(
    returns: list[float],
    risk_free: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """年化夏普(样本标准差)。returns 为周期收益率序列。"""
    if len(returns) < 2:
        return 0.0
    rf_per_period = risk_free / periods_per_year
    excess = [r - rf_per_period for r in returns]
    mean = statistics.fmean(excess)
    sd = statistics.stdev(excess)
    if sd == 0:
        return 0.0
    return mean / sd * math.sqrt(periods_per_year)


def win_rate(trade_pnls: list[float]) -> float:
    if not trade_pnls:
        return 0.0
    wins = sum(1 for p in trade_pnls if p > 0)
    return wins / len(trade_pnls)


def profit_factor(trade_pnls: list[float]) -> float:
    """盈亏比 = 总盈利 / 总亏损(绝对值)。无亏损时返回 inf。"""
    gains = sum(p for p in trade_pnls if p > 0)
    losses = -sum(p for p in trade_pnls if p < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def avg_win_loss(trade_pnls: list[float]) -> tuple[float, float]:
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    avg_w = statistics.fmean(wins) if wins else 0.0
    avg_l = statistics.fmean(losses) if losses else 0.0
    return avg_w, avg_l


def summarize(equity_curve: list[float], trade_pnls: list[float]) -> dict:
    """汇总所有绩效指标为一个 dict。"""
    rets = daily_returns(equity_curve)
    avg_w, avg_l = avg_win_loss(trade_pnls)
    return {
        "total_return": round(total_return(equity_curve), 6),
        "annualized_return": round(annualized_return(equity_curve), 6),
        "max_drawdown": round(max_drawdown(equity_curve), 6),
        "sharpe": round(sharpe(rets), 4),
        "trades": len(trade_pnls),
        "win_rate": round(win_rate(trade_pnls), 4),
        "profit_factor": round(profit_factor(trade_pnls), 4),
        "avg_win": round(avg_w, 4),
        "avg_loss": round(avg_l, 4),
        "total_pnl": round(sum(trade_pnls), 4),
    }
