"""轻量事件式回测内核(纯 Python,无第三方依赖)。

职责:给定信号 + 历史 K 线 → 模拟「信号次日开盘入场、逐日止损/止盈/到期平仓」,
扣 A 股交易成本,产出每笔交易、净值曲线与绩效指标。

设计取舍(Phase 0):
- 入场:信号日之后的**下一交易日开盘价**入场(无未来函数);T+1 起才可平仓(符合 A 股)。
- 平仓(event):逐日检查止损/止盈;同日双触保守判为先止损;达最大持有交易日按收盘平。
- 跳空:开盘已越过止损/止盈则按开盘价成交(gap)。
- 仓位:默认每笔固定名义资金,买 A 股 100 股整数倍(可注入 sizer 供 Phase 1 替换)。
- 净值曲线:逐交易日按收盘价估值，买入占用现金，按实际持有期间计算风险。
- 成交约束:停牌/零成交量与显式价格边界；未提供涨跌停价时不猜测板块规则。

另提供 horizon_return():复刻 strategy_engine.evaluate_strategy_outcomes 口径,用于交叉验证。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from src.modules.strategy.backtest import metrics as M
from src.modules.strategy.backtest.cost_model import CostModel
from src.modules.strategy.backtest.data_adapter import PriceBar, first_index_after

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signal:
    """一条待回测信号(对齐 StrategySignalRun 的可执行字段)。"""

    symbol: str
    market: str
    signal_date: str                  # YYYY-MM-DD(信号产生日)
    entry_price: float | None = None  # None = 用下一交易日开盘价
    stop_loss: float | None = None
    target_price: float | None = None
    holding_days: int = 10            # 最大持有交易日(event 模式)


@dataclass
class BTTrade:
    symbol: str
    market: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    fees: float
    exit_reason: str  # stop_loss | target | expire | eod
    holding_bars: int


@dataclass
class BacktestResult:
    trades: list[BTTrade]
    equity_curve: list[float]
    equity_dates: list[str]
    metrics: dict
    initial_capital: float
    skipped: int = 0
    open_positions: list[BTTrade] = field(default_factory=list)


PositionSizer = Callable[[float], int]  # price -> qty


def fixed_cash_sizer(cash_per_trade: float, lot: int = 100) -> PositionSizer:
    """每笔固定名义资金,买入 lot 的整数倍。"""

    def _size(price: float) -> int:
        if price <= 0:
            return 0
        lots = int((cash_per_trade / price) // lot)
        return max(0, lots * lot)

    return _size


def _parse_day(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


class Backtester:
    def __init__(
        self,
        cost_model: CostModel | None = None,
        initial_capital: float = 1_000_000.0,
        cash_per_trade: float = 100_000.0,
        lot: int = 100,
        sizer: PositionSizer | None = None,
    ) -> None:
        self.cost = cost_model or CostModel()
        self.initial_capital = float(initial_capital)
        self.sizer = sizer or fixed_cash_sizer(cash_per_trade, lot)

    def run_single(self, signal: Signal, bars: list[PriceBar]) -> BTTrade | None:
        """单信号回测:下一交易日开盘入场,逐日止损/止盈/到期平仓。"""
        if not bars:
            return None
        ei = first_index_after(bars, signal.signal_date)
        if ei is None or ei >= len(bars):
            return None
        entry_bar = bars[ei]
        entry_price = entry_bar.open if signal.entry_price is None else float(signal.entry_price)
        if (entry_price <= 0 or not entry_bar.tradable or entry_bar.volume <= 0
                or (entry_bar.limit_up is not None and entry_price >= entry_bar.limit_up)):
            return None
        qty = self.sizer(entry_price)
        if qty <= 0:
            return None

        stop = signal.stop_loss
        target = signal.target_price
        max_hold = max(1, int(signal.holding_days or 10))

        exit_price = exit_date = exit_reason = None
        held = 0
        # T+1 起逐日检查(入场日当天不可卖)
        for j in range(ei + 1, len(bars)):
            held = j - ei
            bar = bars[j]
            if not bar.tradable or bar.volume <= 0 or (bar.limit_down is not None and bar.high <= bar.limit_down):
                continue
            if stop and stop > 0:
                if bar.open <= stop:  # 跳空跌破
                    exit_price, exit_date, exit_reason = bar.open, bar.date, "stop_loss"
                    break
                if bar.low <= stop:
                    exit_price, exit_date, exit_reason = stop, bar.date, "stop_loss"
                    break
            if target and target > 0:
                if bar.open >= target:  # 跳空冲高
                    exit_price, exit_date, exit_reason = bar.open, bar.date, "target"
                    break
                if bar.high >= target:
                    exit_price, exit_date, exit_reason = target, bar.date, "target"
                    break
            if held >= max_hold:
                exit_price, exit_date, exit_reason = bar.close, bar.date, "expire"
                break

        if exit_price is None:
            last = bars[-1]
            can_sell = (len(bars) - 1 > ei and last.tradable and last.volume > 0
                        and (last.limit_down is None or last.high > last.limit_down))
            exit_price, exit_date, exit_reason = last.close, last.date, "eod" if can_sell else "open"
            held = len(bars) - 1 - ei

        rt = self.cost.round_trip_pnl(entry_price, exit_price, qty)
        if exit_reason == "open":
            buy = rt["buy"]
            pnl = exit_price * qty + buy.cash_delta
            rt.update(pnl=round(pnl, 4), pnl_pct=pnl / -buy.cash_delta * 100, total_cost=buy.friction)
        return BTTrade(
            symbol=signal.symbol,
            market=signal.market,
            entry_date=entry_bar.date,
            entry_price=round(entry_price, 4),
            exit_date=exit_date,
            exit_price=round(exit_price, 4),
            quantity=qty,
            pnl=rt["pnl"],
            pnl_pct=rt["pnl_pct"],
            fees=rt["total_cost"],
            exit_reason=exit_reason,
            holding_bars=held,
        )

    def run(
        self, signals: list[Signal], bars_by_symbol: dict
    ) -> BacktestResult:
        """批量回测,聚合净值曲线与绩效指标。

        bars_by_symbol: 键可为 (symbol, market) 或 symbol。
        """
        planned: list[BTTrade] = []
        histories: dict[tuple[str, str], dict[str, PriceBar]] = {}
        skipped = 0
        for sig in signals:
            bars = bars_by_symbol.get((sig.symbol, sig.market)) or bars_by_symbol.get(sig.symbol) or []
            trade = self.run_single(sig, bars)
            if trade is None:
                skipped += 1
                continue
            planned.append(trade)
            histories[(sig.symbol, sig.market)] = {bar.date: bar for bar in bars}

        baseline = min((sig.signal_date for sig in signals), default="")
        end = max((trade.exit_date for trade in planned), default=baseline)
        days = sorted({day for history in histories.values() for day in history if baseline < day <= end})
        entries: dict[str, list[BTTrade]] = {}
        for trade in sorted(planned, key=lambda t: (t.entry_date, t.market, t.symbol)):
            entries.setdefault(trade.entry_date, []).append(trade)
        cash = self.initial_capital
        curve, dates = [cash], [baseline]
        held: dict[tuple[str, str], tuple[BTTrade, float]] = {}
        trades: list[BTTrade] = []
        for day in days:
            # Open purchases cannot spend the proceeds of a later intraday sale.
            for trade in entries.get(day, []):
                key = (trade.symbol, trade.market)
                buy = self.cost.fill("buy", trade.entry_price, trade.quantity)
                if key in held or -buy.cash_delta > cash:
                    skipped += 1
                    continue
                cash += buy.cash_delta
                held[key] = (trade, trade.entry_price)
            for key, (trade, last_price) in list(held.items()):
                bar = histories[key].get(day)
                if bar is not None:
                    last_price = bar.close
                held[key] = (trade, last_price)
                if trade.exit_date == day and trade.exit_reason != "open":
                    cash += self.cost.fill("sell", trade.exit_price, trade.quantity).cash_delta
                    trades.append(trade)
                    del held[key]
            equity = cash + sum(trade.quantity * mark for trade, mark in held.values())
            curve.append(round(equity, 4))
            dates.append(day)

        return BacktestResult(
            trades=trades,
            equity_curve=curve,
            equity_dates=dates,
            metrics=M.summarize(curve, [trade.pnl for trade in trades]),
            initial_capital=self.initial_capital,
            skipped=skipped,
            open_positions=[trade for trade, _ in held.values()],
        )


def horizon_return(signal: Signal, bars: list[PriceBar], horizon_days: int) -> float | None:
    """复刻 strategy_engine.evaluate_strategy_outcomes 口径,用于交叉验证。

    base = signal.entry_price;target_day = signal_date + horizon_days(自然日);
    outcome = 最近 <= target_day 的收盘价;return% = (outcome-base)/base*100。
    """
    snap = _parse_day(signal.signal_date)
    base = signal.entry_price
    if snap is None or not bars or not base or base <= 0:
        return None
    target_day = snap + timedelta(days=int(horizon_days))
    outcome = None
    for b in bars:
        d = _parse_day(b.date)
        if d is None:
            continue
        if d <= target_day:
            outcome = b.close
        else:
            break
    if outcome is None:
        return None
    return (outcome - base) / base * 100.0
