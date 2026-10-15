"""Market-pool target sizing. No daily cash allowance or cross-market borrowing."""

import math

from . import service
from .costs import QlibCostModel
from .policy import lot_quantity

CAPITAL_POLICY = "market_pool_target_v2"
PAPER_POLICY = "topk_dropout_next_open_market_pool_target_v2"
TARGET_RATIO = 0.9


def target_membership(rows, managed, eligible, topk):
    # Do not sell a higher-ranked keeper merely to manufacture daily turnover.
    locked = set(managed) - eligible
    ranked = sorted(
        (r for r in rows if r["symbol"] in eligible),
        key=lambda r: (r["rank"], r["symbol"]),
    )
    targets = {r["symbol"] for r in ranked[: max(0, topk - len(locked))]}
    return sorted(set(managed) & eligible - targets), sorted(targets - set(managed))


def capital_config(account):
    from src.modules.paper_trading.paper_trading_engine import (
        market_allocations_or_default,
    )

    allocations = market_allocations_or_default(account)
    data = {
        "account_id": account.id,
        "initial_capital": account.initial_capital,
        "allocations": allocations,
        "capital_policy": CAPITAL_POLICY,
    }
    return {
        **data,
        "capital_config_hash": service.content_hash(data),
        "market_principal": account.initial_capital * allocations.get("CN", 0),
        "target_ratio": TARGET_RATIO,
    }


def holding_state(positions):
    return [
        {
            "id": p.id,
            "symbol": p.stock_symbol,
            "quantity": p.quantity,
            "buy_outlay": (
                p.research_meta.get("buy_outlay") if p.research_meta else None
            ),
        }
        for p in sorted(positions, key=lambda p: p.id)
        if p.stock_market == "CN"
    ]


def pool_values(db, account, positions, prices):
    from src.modules.paper_trading.paper_trading_engine import market_available_cash

    values = {}
    for p in positions:
        if p.stock_market != "CN" or p.status != "open":
            continue
        symbol = (p.research_meta or {}).get("instrument") or (
            ("SH" if p.stock_symbol.startswith("6") else "SZ") + p.stock_symbol
        )
        price = prices.get(symbol)
        if (
            not isinstance(price, (float, int))
            or not math.isfinite(price)
            or price <= 0
        ):
            raise ValueError("qlib_portfolio_valuation_unavailable")
        values[p.id] = price * p.quantity
    cash = market_available_cash(db, account, "CN")
    equity = cash + sum(values.values())
    return {
        "cash": max(0.0, cash),
        "equity": equity,
        "exposure": sum(values.values()),
        "target_exposure": max(0.0, equity * TARGET_RATIO),
        "values": values,
    }


def target_plan(
    db, account, positions, managed, prices, sells, buys, eligible, names, config
):
    pool = pool_values(db, account, positions, prices)
    costs = QlibCostModel(config)
    # Locked/ineligible and legacy positions consume exposure but are never taken over.
    targets = (set(managed) - set(sells)) | set(buys)
    adjustable = targets & eligible
    fixed = sum(
        pool["values"][p.id]
        for p in positions
        if p.stock_market == "CN"
        and ((p.research_meta or {}).get("instrument") not in adjustable | set(sells))
    )
    per_target = max(0.0, pool["target_exposure"] - fixed) / max(1, len(adjustable))
    target_qty = {s: int(per_target / prices[s]) // 100 * 100 for s in adjustable}
    target_qty.update({s: 0 for s in sells})
    orders, remaining_exposure, proceeds, sell_fees = [], pool["exposure"], 0.0, 0.0
    for symbol in sorted(target_qty):
        p = managed.get(symbol)
        current = p.quantity if p else 0
        delta = target_qty[symbol] - current
        if delta >= 0:
            continue
        quantity = current if target_qty[symbol] == 0 else (-delta) // 100 * 100
        if not quantity:
            continue
        fill = costs.fill("sell", prices[symbol], quantity)
        proceeds += fill.cash_delta
        sell_fees += fill.explicit_fees
        remaining_exposure -= quantity * prices[symbol]
        orders.append(
            {
                "symbol": symbol,
                "name": names.get(symbol, symbol),
                "side": "sell",
                "reference_price": prices[symbol],
                "estimated_quantity": quantity,
                "estimated_fee": fill.explicit_fees,
                "estimated_cash_delta": fill.cash_delta,
                "cash_limit": None,
                "position_id": p.id,
                "held_quantity": current,
                "target_quantity": target_qty[symbol],
            }
        )
    budget = max(
        0.0,
        min(
            pool["cash"] + proceeds,
            (pool["equity"] - sell_fees) * TARGET_RATIO - remaining_exposure,
        ),
    )
    gaps = {
        s: (q - (managed[s].quantity if s in managed else 0)) * prices[s]
        for s, q in target_qty.items()
        if q > (managed[s].quantity if s in managed else 0)
    }
    total_gap = sum(gaps.values())
    for symbol, gap in sorted(gaps.items()):
        ceiling = gap * min(1, budget / total_gap) if total_gap else 0
        quantity = lot_quantity(ceiling, prices[symbol], ceiling, costs)
        if not quantity:
            continue
        p = managed.get(symbol)
        fill = costs.fill("buy", prices[symbol], quantity)
        orders.append(
            {
                "symbol": symbol,
                "name": names.get(symbol, symbol),
                "side": "buy",
                "reference_price": prices[symbol],
                "estimated_quantity": quantity,
                "estimated_fee": fill.explicit_fees,
                "estimated_cash_delta": fill.cash_delta,
                "cash_limit": ceiling,
                "position_id": p.id if p else None,
                "held_quantity": p.quantity if p else 0,
                "target_quantity": target_qty[symbol],
                "target_weight": (
                    per_target / pool["equity"] if pool["equity"] > 0 else 0
                ),
            }
        )
    return {
        "orders": orders,
        "cash_budget": sum(o["cash_limit"] or 0 for o in orders),
        "capital_policy": CAPITAL_POLICY,
        "capital": capital_config(account),
        "market_equity": pool["equity"],
        "current_exposure": pool["exposure"],
        "target_exposure": pool["target_exposure"],
        "market_holdings": holding_state(positions),
        "target_weights": (
            {s: per_target / pool["equity"] for s in adjustable}
            if pool["equity"] > 0
            else {}
        ),
    }
