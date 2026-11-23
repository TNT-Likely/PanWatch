"""Fact-only snapshots assembled from the instance's own authorised data."""

from collections import defaultdict
from collections.abc import Callable
from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, FiniteFloat, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.mobile.disclosures import Disclosures, load_disclosures
from src.modules.mobile.portfolio_metrics import Metric, _finite, _metric
from src.modules.mobile.schemas import WatchlistSymbol
from src.modules.mobile.symbol_facts import Market, QuoteSnapshot, _CURRENCIES, quote_snapshot
from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.persistence.models import Stock


FactPhase = Literal["premarket", "intraday", "postmarket"]
FactState = Literal["ready", "empty", "partial", "unavailable"]
Symbol = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class FactTarget(BaseModel):
    market: Market
    symbol: Symbol


class PremarketSnapshot(BaseModel):
    phase: Literal["premarket"] = "premarket"
    state: FactState
    disclosures: Disclosures


class SymbolObservation(BaseModel):
    target: WatchlistSymbol
    currency: Literal["CNY", "HKD", "USD"]
    quote: QuoteSnapshot | None


class IntradaySnapshot(BaseModel):
    phase: Literal["intraday"] = "intraday"
    state: FactState
    observations: list[SymbolObservation]


class PositionContribution(BaseModel):
    target: WatchlistSymbol
    daily_pnl: Metric
    quote_price: FiniteFloat | None = None
    quote_date: date | None = None


class PostmarketSnapshot(BaseModel):
    phase: Literal["postmarket"] = "postmarket"
    state: FactState
    currency: Literal["CNY"] = "CNY"
    basis: Literal["current_holdings"] = "current_holdings"
    total_daily_pnl: Metric
    contributions: list[PositionContribution]


class PortfolioContributions(PostmarketSnapshot):
    computed_at: AwareDatetime
    account_id: int | None = None


FactSnapshot = Annotated[PremarketSnapshot | IntradaySnapshot | PostmarketSnapshot, Field(discriminator="phase")]


def _availability(count: int, observed: int) -> FactState:
    if count == 0:
        return "empty"
    if observed == count:
        return "ready"
    return "partial" if observed else "unavailable"


def symbol_in_watchlist(db: Session, target: FactTarget) -> bool:
    return db.scalar(select(Stock.id).where(Stock.market == target.market, Stock.symbol == target.symbol).limit(1)) is not None


def _intraday(db: Session, target: FactTarget | None) -> IntradaySnapshot:
    query = select(Stock).where(Stock.market.in_(_CURRENCIES)).order_by(Stock.sort_order, Stock.id)
    if target:
        query = query.where(Stock.market == target.market, Stock.symbol == target.symbol)
    by_market = defaultdict(dict)
    for stock in db.scalars(query):
        by_market[stock.market].setdefault(stock.symbol, WatchlistSymbol.model_validate(stock, from_attributes=True))
    observations = []
    for market, universe in by_market.items():
        quotes = {row["symbol"]: row for row in md_quote_rows(list(universe), market) if row["market"] == market}
        observations.extend(SymbolObservation(
            target=stock, currency=_CURRENCIES[market], quote=quote_snapshot(market, quotes.get(symbol)),
        ) for symbol, stock in universe.items())
    return IntradaySnapshot(
        state=_availability(len(observations), sum(item.quote is not None for item in observations)),
        observations=observations,
    )


def _daily_pnl(positions: list[dict]) -> Metric:
    amount = round(sum(item["daily_pnl"] for item in positions if _finite(item.get("daily_pnl"))), 2)
    return _metric(positions, "daily_pnl", amount)


def build_postmarket_snapshot(summary: dict, target: FactTarget | None = None) -> PostmarketSnapshot:
    positions = [
        item for account in summary["accounts"] for item in account["positions"]
        if target is None or (item["market"], item["symbol"]) == (target.market, target.symbol)
    ]
    by_symbol = defaultdict(list)
    for item in positions:
        by_symbol[item["market"], item["symbol"]].append(item)
    contributions = []
    for group in by_symbol.values():
        position = group[0]
        price = position.get("current_price")
        try:
            quote_date = date.fromisoformat(position.get("quote_date") or "")
        except (TypeError, ValueError):
            quote_date = None
        contributions.append(PositionContribution(
            target=WatchlistSymbol.model_validate(position), daily_pnl=_daily_pnl(group),
            quote_price=price if _finite(price) else None, quote_date=quote_date,
        ))
    total = (_daily_pnl(positions) if target is not None
             else _metric(positions, "daily_pnl", summary["total"].get("total_daily_pnl", 0 if not positions else None)))
    return PostmarketSnapshot(
        state=_availability(len(positions), sum(_finite(item.get("daily_pnl")) for item in positions)),
        total_daily_pnl=total, contributions=contributions,
    )


def capture_snapshot(
    db: Session, phase: FactPhase, target: FactTarget | None, get_portfolio_summary: Callable[[Session], dict],
) -> FactSnapshot:
    if phase == "premarket":
        disclosures = load_disclosures(db, target.market if target else "CN", target.symbol if target else None, 7)
        if disclosures.state == "ready":
            state = "ready" if disclosures.items else "empty"
        else:
            state = "partial" if disclosures.items else "unavailable"
        return PremarketSnapshot(state=state, disclosures=disclosures)
    if phase == "intraday":
        return _intraday(db, target)
    return build_postmarket_snapshot(get_portfolio_summary(db), target)
