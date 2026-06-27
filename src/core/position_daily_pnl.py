"""持仓今日盈亏：区分隔夜仓与当日买卖。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeLot:
    side: str  # buy | sell
    quantity: int
    price: float


def compute_position_daily_pnl(
    *,
    current_price: float,
    quantity: int,
    prev_close: float | None,
    today_trades: list[TradeLot],
    day_start_qty: int,
) -> tuple[float | None, float | None]:
    """计算单只持仓今日盈亏（原币种）。

    day_start_qty: 今日首笔流水前的持仓股数；无今日流水时为当前持仓股数。

    - 隔夜仓：按昨收 -> 现价
    - 当日买入仍持有：按买入价 -> 现价
    - 当日卖出：按卖出价相对昨收/买入价计算已实现部分
    """
    if quantity <= 0 or current_price <= 0:
        return None, None

    if not today_trades:
        if prev_close is None or prev_close <= 0:
            return None, None
        daily_pnl = (current_price - prev_close) * quantity
        daily_pnl_pct = (current_price - prev_close) / prev_close * 100
        return round(daily_pnl, 2), round(daily_pnl_pct, 2)

    overnight = max(0, int(day_start_qty))
    buy_lots: list[list[int | float]] = []
    realized = 0.0
    cost_basis = overnight * prev_close if prev_close and prev_close > 0 else 0.0

    for trade in today_trades:
        qty = int(trade.quantity)
        price = float(trade.price)
        if qty <= 0 or price <= 0:
            continue
        if trade.side == "buy":
            buy_lots.append([qty, price])
            cost_basis += qty * price
            continue

        sell_qty = qty
        sell_price = price
        from_overnight = min(overnight, sell_qty)
        if from_overnight > 0 and prev_close and prev_close > 0:
            realized += (sell_price - prev_close) * from_overnight
            cost_basis -= from_overnight * prev_close
        overnight -= from_overnight
        sell_qty -= from_overnight

        while sell_qty > 0 and buy_lots:
            lot_qty, lot_price = buy_lots[0]
            take = min(int(lot_qty), sell_qty)
            realized += (sell_price - lot_price) * take
            cost_basis -= take * lot_price
            lot_qty -= take
            sell_qty -= take
            if lot_qty <= 0:
                buy_lots.pop(0)
            else:
                buy_lots[0][0] = lot_qty

    unrealized = 0.0
    if overnight > 0 and prev_close and prev_close > 0:
        unrealized += (current_price - prev_close) * overnight
    for lot_qty, lot_price in buy_lots:
        unrealized += (current_price - float(lot_price)) * int(lot_qty)

    daily_pnl = realized + unrealized
    daily_pnl_pct = (daily_pnl / cost_basis * 100) if cost_basis > 0 else None
    return round(daily_pnl, 2), round(daily_pnl_pct, 2) if daily_pnl_pct is not None else None
