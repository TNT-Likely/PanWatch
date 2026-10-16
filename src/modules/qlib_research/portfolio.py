"""Market-pool target sizing. No daily cash allowance or cross-market borrowing."""

import math

from . import service
from .allocation import (
    CAPITAL_POLICY,
    PAPER_POLICY,
    TARGET_RATIO,
    allocate_targets,
    target_membership,
)


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
    holdings = {}
    for p in positions:
        if p.stock_market != "CN" or p.status != "open":
            continue
        symbol = (p.research_meta or {}).get("instrument") or (
            ("SH" if p.stock_symbol.startswith("6") else "SZ") + p.stock_symbol
        )
        holdings[symbol] = holdings.get(symbol, 0) + p.quantity
    plan = allocate_targets(
        pool["equity"] - pool["exposure"],
        holdings,
        managed,
        prices,
        sells,
        buys,
        eligible,
        config,
    )
    orders = [
        {
            **order,
            "name": names.get(order["symbol"], order["symbol"]),
            "position_id": managed[order["symbol"]].id
            if order["symbol"] in managed
            else None,
        }
        for order in plan["orders"]
    ]
    return {
        "orders": orders,
        **{k: v for k, v in plan.items() if k != "orders"},
        "capital_policy": CAPITAL_POLICY,
        "capital": capital_config(account),
        "market_equity": pool["equity"],
        "current_exposure": pool["exposure"],
        "target_exposure": pool["target_exposure"],
        "market_holdings": holding_state(positions),
    }
