"""轻量事件式回测内核(纯 Python,无第三方依赖)。

职责:给定信号 + 历史 K 线 → 模拟「信号次日开盘入场、逐日止损/止盈/到期平仓」,
扣 A 股交易成本,产出每笔交易、净值曲线与绩效指标。

设计取舍(Phase 0):
- 入场:信号日之后的**下一交易日开盘价**入场(无未来函数);T+1 起才可平仓(符合 A 股)。
- 平仓(event):逐日检查止损/止盈;同日双触保守判为先止损;达最大持有交易日按收盘平。
- 跳空:开盘已越过止损/止盈则按开盘价成交(gap)。
- 仓位:默认每笔固定名义资金,买 A 股 100 股整数倍(可注入 sizer 供 Phase 1 替换)。
- 净值曲线:按平仓日累积已实现盈亏(简化);并发持仓的逐日浮动 mark 留作后续扩展。
- 涨跌停无法成交约束未建模(TODO:需前收 + 板块判定)。

另提供 horizon_return():复刻 strategy_engine.evaluate_strategy_outcomes 口径,用于交叉验证。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
        if entry_price <= 0:
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
            exit_price, exit_date, exit_reason = last.close, last.date, "eod"
            held = len(bars) - 1 - ei

        rt = self.cost.round_trip_pnl(entry_price, exit_price, qty)
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
        trades: list[BTTrade] = []
        skipped = 0
        for sig in signals:
            bars = bars_by_symbol.get((sig.symbol, sig.market)) or bars_by_symbol.get(sig.symbol)
            if not bars:
                skipped += 1
                continue
            t = self.run_single(sig, bars)
            if t is None:
                skipped += 1
                continue
            trades.append(t)

        trades_sorted = sorted(trades, key=lambda t: t.exit_date)
        equity = self.initial_capital
        curve = [self.initial_capital]
        dates = [""]
        for t in trades_sorted:
            equity += t.pnl
            curve.append(round(equity, 4))
            dates.append(t.exit_date)

        pnls = [t.pnl for t in trades]
        return BacktestResult(
            trades=trades,
            equity_curve=curve,
            equity_dates=dates,
            metrics=M.summarize(curve, pnls),
            initial_capital=self.initial_capital,
            skipped=skipped,
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
