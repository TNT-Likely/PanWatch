"""Mobile portfolio amounts projected from the self-hosted Web summary."""

from math import isfinite
from typing import Literal

from pydantic import BaseModel, FiniteFloat

Coverage = Literal["complete", "partial", "unavailable"]


class Metric(BaseModel):
    value: FiniteFloat | None
    coverage: Coverage


class AccountMetrics(BaseModel):
    account_id: int
    name: str
    available_assets: FiniteFloat
    valuation: Coverage
    today_pnl: Metric
    unrealized_pnl: Metric


class PortfolioMetrics(BaseModel):
    currency: Literal["CNY"] = "CNY"
    basis: Literal["current_holdings"] = "current_holdings"
    available_assets: FiniteFloat
    valuation: Coverage
    today_pnl: Metric
    unrealized_pnl: Metric
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


def _summary(row: dict, positions: list[dict]) -> dict:
    daily = row.get("total_daily_pnl", 0 if not positions else None)
    return {
        "available_assets": row["total_assets"],
        "valuation": _coverage(positions, "market_value"),
        "today_pnl": _metric(positions, "daily_pnl", daily),
        "unrealized_pnl": _metric(positions, "market_value", row.get("total_pnl")),
    }


def build_portfolio_metrics(summary: dict) -> PortfolioMetrics:
    accounts = summary["accounts"]
    positions = [position for account in accounts for position in account["positions"]]
    return PortfolioMetrics(
        **_summary(summary["total"], positions),
        accounts=[
            AccountMetrics(
                account_id=account["id"], name=account["name"],
                **_summary(account, account["positions"]),
            )
            for account in accounts
        ],
    )
