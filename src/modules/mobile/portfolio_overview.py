"""One consistent holdings and contribution snapshot for the mobile workspace."""

from datetime import UTC, datetime

from pydantic import AwareDatetime, FiniteFloat

from src.modules.mobile.fact_snapshots import PositionContribution, build_postmarket_snapshot
from src.modules.mobile.portfolio_metrics import Metric
from src.modules.mobile.schemas import Holding, Portfolio


class PortfolioOverview(Portfolio):
    market_value_cny: FiniteFloat | None
    available_funds_cny: FiniteFloat
    today_pnl: Metric
    contributions: list[PositionContribution]
    computed_at: AwareDatetime


def build_portfolio(summary: dict) -> Portfolio:
    holdings = [
        Holding(
            symbol=position["symbol"],
            name=position["name"],
            market=position["market"],
            quantity=position["quantity"],
            market_value=position["market_value"],
            currency={"HK": "HKD", "US": "USD"}.get(position["market"], "CNY"),
        )
        for account in summary["accounts"] for position in account["positions"]
    ]
    covered = sum(holding.market_value is not None for holding in holdings)
    valuation = "complete" if covered == len(holdings) else "partial" if covered else "unavailable"
    return Portfolio(total_assets=summary["total"]["total_assets"], valuation=valuation, holdings=holdings)


def build_overview(summary: dict) -> PortfolioOverview:
    portfolio = build_portfolio(summary)
    facts = build_postmarket_snapshot(summary)
    total = summary["total"]
    return PortfolioOverview(
        **portfolio.model_dump(),
        market_value_cny=total["total_market_value"] if portfolio.valuation != "unavailable" else None,
        available_funds_cny=total["available_funds"],
        today_pnl=facts.total_daily_pnl,
        contributions=facts.contributions,
        computed_at=datetime.now(UTC),
    )
