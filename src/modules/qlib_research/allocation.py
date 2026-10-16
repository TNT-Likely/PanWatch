"""Pure, raw-share target allocation shared by paper previews and Qlib replay.

No database, clock, providers or Qlib imports. Execution remains next-open and
may reject or reduce these reference-close orders; caps are never redistributed.
"""

import math

CAPITAL_POLICY = "market_pool_target_v2"
PAPER_POLICY = "topk_dropout_next_open_market_pool_target_v2"
TARGET_RATIO = 0.9


def fee_amount(side, price, quantity, config):
    return max(
        price * quantity * config["open_cost" if side == "buy" else "close_cost"],
        config["min_cost"],
    )


def target_membership(rows, managed, eligible, topk):
    locked = set(managed) - eligible
    ranked = sorted(
        (r for r in rows if r["symbol"] in eligible and r.get("signal_active", True)),
        key=lambda r: (r["rank"], r["symbol"]),
    )
    targets = {r["symbol"] for r in ranked[: max(0, topk - len(locked))]}
    return sorted(set(managed) & eligible - targets), sorted(targets - set(managed))


def buy_quantity(budget, price, config, *, lot=100, maximum=None):
    if not math.isfinite(price) or price <= 0 or budget <= 0:
        return 0
    quantity = max(0, int(budget / price) // lot * lot)
    if maximum is not None:
        quantity = min(quantity, maximum)
    while (
        quantity > 0
        and quantity * price + fee_amount("buy", price, quantity, config) > budget
    ):
        quantity -= lot
    return max(0, quantity)


def allocate_targets(
    cash, holdings, managed, prices, sells, buys, eligible, config, *, lots=None
):
    """Holdings are raw symbol->quantity; unmanaged/locked exposure is reserved."""
    lots = lots or {}
    values = {}
    for symbol, quantity in holdings.items():
        price = prices.get(symbol)
        if (
            not isinstance(price, (int, float))
            or not math.isfinite(price)
            or price <= 0
        ):
            raise ValueError("qlib_portfolio_valuation_unavailable")
        values[symbol] = quantity * price
    exposure = sum(values.values())
    equity = cash + exposure
    target = max(0.0, equity * TARGET_RATIO)
    targets = (set(managed) - set(sells)) | set(buys)
    adjustable = targets & eligible
    fixed = sum(
        value
        for symbol, value in values.items()
        if symbol not in adjustable | set(sells)
    )
    per_target = max(0.0, target - fixed) / max(1, len(adjustable))
    desired = {
        s: int(per_target / prices[s]) // lots.get(s, 100) * lots.get(s, 100)
        for s in adjustable
    }
    desired.update({s: 0 for s in sells})
    orders, remaining, proceeds, sell_fees = [], exposure, 0.0, 0.0
    for symbol in sorted(desired):
        current = holdings.get(symbol, 0)
        delta = desired[symbol] - current
        if delta >= 0:
            continue
        lot = lots.get(symbol, 100)
        quantity = current if desired[symbol] == 0 else (-delta) // lot * lot
        if not quantity:
            continue
        fee = fee_amount("sell", prices[symbol], quantity, config)
        proceeds += quantity * prices[symbol] - fee
        sell_fees += fee
        remaining -= quantity * prices[symbol]
        orders.append(
            dict(
                symbol=symbol,
                side="sell",
                reference_price=prices[symbol],
                estimated_quantity=quantity,
                estimated_fee=fee,
                estimated_cash_delta=quantity * prices[symbol] - fee,
                cash_limit=None,
                held_quantity=current,
                target_quantity=desired[symbol],
            )
        )
    budget = max(
        0.0,
        min(max(0.0, cash) + proceeds, (equity - sell_fees) * TARGET_RATIO - remaining),
    )
    gaps = {
        s: (q - holdings.get(s, 0)) * prices[s]
        for s, q in desired.items()
        if q > holdings.get(s, 0)
    }
    total_gap = sum(gaps.values())
    for symbol, gap in sorted(gaps.items()):
        ceiling = gap * min(1, budget / total_gap) if total_gap else 0
        quantity = buy_quantity(
            ceiling, prices[symbol], config, lot=lots.get(symbol, 100)
        )
        if not quantity:
            continue
        fee = fee_amount("buy", prices[symbol], quantity, config)
        orders.append(
            dict(
                symbol=symbol,
                side="buy",
                reference_price=prices[symbol],
                estimated_quantity=quantity,
                estimated_fee=fee,
                estimated_cash_delta=-(quantity * prices[symbol] + fee),
                cash_limit=ceiling,
                held_quantity=holdings.get(symbol, 0),
                target_quantity=desired[symbol],
                target_weight=per_target / equity if equity > 0 else 0,
            )
        )
    return dict(
        orders=orders,
        cash_budget=sum(o["cash_limit"] or 0 for o in orders),
        market_equity=equity,
        current_exposure=exposure,
        target_exposure=target,
        target_weights={s: per_target / equity for s in adjustable}
        if equity > 0
        else {},
    )
