"""Mobile portfolio amounts projected from the self-hosted Web summary."""

from datetime import UTC, datetime
from math import isfinite
from typing import Literal

from pydantic import AwareDatetime, BaseModel, FiniteFloat

Coverage = Literal["complete", "partial", "unavailable"]


class Metric(BaseModel):
    value: FiniteFloat | None
    coverage: Coverage


class PortfolioAmounts(BaseModel):
    available_assets: FiniteFloat
    valuation: Coverage
    today_pnl: Metric
    unrealized_pnl: Metric
    market_value_cny: FiniteFloat | None = None
    available_funds_cny: FiniteFloat | None = None
    cost_basis_cny: FiniteFloat | None = None


class AccountMetrics(PortfolioAmounts):
    account_id: int
    name: str


class PortfolioMetrics(PortfolioAmounts):
    currency: Literal["CNY"] = "CNY"
    basis: Literal["current_holdings"] = "current_holdings"
    account_contributions: Literal[True] = True
    computed_at: AwareDatetime
    accounts: list[AccountMetrics]


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _coverage(positions: list[dict], field: str) -> Coverage:
    valid = sum(_finite(position.get(field)) for position in positions)
    if valid == len(positions):
        return "complete"
    return "partial" if valid else "unavailable"


def _metric(positions: list[dict], field: str, total) -> Metric:
    coverage = _coverage(positions, field)
    # Web aggregates can be partial, including an unrealized loss from missing quotes.
    # Expose the official total only when every position contributes to it.
    if coverage != "complete":
        return Metric(value=None, coverage=coverage)
    if not _finite(total):
        return Metric(value=None, coverage="unavailable")
    return Metric(value=total, coverage="complete")


def _has_recorded_cost(position: dict) -> bool:
    rate = 1 if position.get("market") == "CN" else position.get("exchange_rate")
    return (_finite(position.get("cost_price")) and _finite(position.get("quantity"))
            and _finite(rate) and rate > 0)


def _summary(row: dict, positions: list[dict]) -> dict:
    daily = row.get("total_daily_pnl", 0 if not positions else None)
    valuation = _coverage(positions, "market_value")
    market_value = row.get("total_market_value")
    available_funds = row.get("available_funds")
    total_cost = row.get("total_cost")
    return {
        "available_assets": row["total_assets"],
        "valuation": valuation,
        "cost_basis_cny": total_cost if _finite(total_cost) and all(_has_recorded_cost(p) for p in positions) else None,
        "market_value_cny": market_value if valuation == "complete" and _finite(market_value) else None,
        "available_funds_cny": available_funds if _finite(available_funds) else None,
        "today_pnl": _metric(positions, "daily_pnl", daily),
        "unrealized_pnl": _metric(positions, "market_value", row.get("total_pnl")),
    }


def build_portfolio_metrics(summary: dict) -> PortfolioMetrics:
    accounts = summary["accounts"]
    positions = [position for account in accounts for position in account["positions"]]
    return PortfolioMetrics(
        **_summary(summary["total"], positions),
        computed_at=datetime.now(UTC),
        accounts=[
            AccountMetrics(
                account_id=account["id"], name=account["name"],
                **_summary(account, account["positions"]),
            )
            for account in accounts
        ],
    )
