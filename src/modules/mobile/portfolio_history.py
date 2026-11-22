"""Value today's quantities and cash at dated closes with the instance's current FX."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from math import isfinite
from typing import Literal

from pydantic import BaseModel, FiniteFloat

from src.platform.marketdata.marketdata_client import get_market_data


class LookbackPoint(BaseModel):
    date: date
    assets: FiniteFloat | None
    covered_positions: int


class PortfolioLookback(BaseModel):
    basis: Literal["current_holdings_fixed_fx"] = "current_holdings_fixed_fx"
    currency: Literal["CNY"] = "CNY"
    fixed_cash: FiniteFloat
    position_count: int
    sources: list[str]
    points: list[LookbackPoint]


def build_portfolio_lookback(summary: dict) -> PortfolioLookback:
    cash = summary["total"]["available_funds"]
    quantities = defaultdict(float)
    count = defaultdict(int)
    position_count = 0
    for account in summary["accounts"]:
        for position in account["positions"]:
            position_count += 1
            key = (position["market"], position["symbol"])
            rate = 1 if position["market"] == "CN" else position["exchange_rate"]
            if rate is not None and isfinite(rate) and rate > 0:
                quantities[key] += position["quantity"] * rate
                count[key] += 1
    if not quantities:
        return PortfolioLookback(fixed_cash=cash, position_count=position_count, sources=[], points=[])

    client = get_market_data()

    def history(key):
        market, symbol = key
        bars, source = client.klines_with_vendor(symbol, market=market, days=35)
        closes = {}
        for bar in bars:
            if not isfinite(bar.close):
                continue
            try:
                day = date.fromisoformat(bar.date)
            except ValueError:
                continue
            closes[day] = bar.close
        return closes, source

    values = defaultdict(float)
    seen = defaultdict(int)
    sources = set()
    with ThreadPoolExecutor(max_workers=min(5, len(quantities))) as pool:
        for key, (closes, source) in zip(quantities, pool.map(history, quantities), strict=True):
            if source and closes:
                sources.add(source)
            for day, close in closes.items():
                values[day] += close * quantities[key]
                seen[day] += count[key]
    points = [
        LookbackPoint(
            date=day, assets=round(values[day] + cash, 2) if seen[day] == position_count else None,
            covered_positions=seen[day],
        )
        for day in sorted(values)[-35:]
    ]
    return PortfolioLookback(
        fixed_cash=cash, position_count=position_count, sources=sorted(sources), points=points,
    )
