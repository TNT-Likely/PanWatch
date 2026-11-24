"""Objective candidate search and explicit updates to the paired watchlist."""

from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, Field, FiniteFloat, StringConstraints
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from src.modules.market.watchlist import add_stock
from src.modules.mobile.schemas import MobileResponse, WatchlistSymbol
from src.platform.marketdata.instruments import AssetType
from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.quote_display import quote_observed_at
from src.platform.marketdata.stock_list import search_stocks
from src.platform.persistence.database import get_db
from src.platform.persistence.models import Position, PriceAlertHit, PriceAlertRule, Stock, StockAgent
from src.web.errors import api_error

DB = Annotated[Session, Depends(get_db)]
Market = Literal["CN", "HK", "US"]
SearchTerm = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=48)]
ScreeningQuery = Annotated[str, StringConstraints(strip_whitespace=True, max_length=48)]
Price = Annotated[FiniteFloat, Field(ge=0)]
SortField = Literal["symbol", "change_pct", "turnover", "volume_ratio", "observed_at"]
SortDirection = Literal["asc", "desc"]
RecordRange = Literal["24h", "7d"]
_CURRENCIES = {"CN": "CNY", "HK": "HKD", "US": "USD"}
_RECORD_RANGES = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}


class Candidate(BaseModel):
    target: WatchlistSymbol
    asset_type: AssetType | None
    price: FiniteFloat | None
    change_pct: FiniteFloat | None
    turnover: FiniteFloat | None
    volume_ratio: FiniteFloat | None
    quote_date: date | None
    observed_at: AwareDatetime | None
    source: str | None
    saved: bool


class Screening(BaseModel):
    candidates: list[Candidate]
    currency: Literal["CNY", "HKD", "USD"]
    missing_quotes: int
    sort: SortField
    direction: SortDirection
    asset_type: Literal["etf"] | None = None
    observed_within: RecordRange | None = None


class WatchlistTarget(BaseModel):
    market: Market
    symbol: SearchTerm


def catalog_symbols(query: str, market: Market) -> dict[str, WatchlistSymbol]:
    candidates = {}
    for item in search_stocks(query, market, limit=20):
        if item.get("market") != market:
            continue
        target = WatchlistSymbol.model_validate(item)
        candidates.setdefault(target.symbol, target)
    return candidates


def _watchlist_records(db: Session, market: Market, symbols: list[str] | None = None) -> dict[str, Stock]:
    query = select(Stock).where(Stock.market == market).order_by(Stock.sort_order, Stock.id)
    if symbols is not None:
        query = query.where(Stock.symbol.in_(symbols))
    candidates = {}
    for row in db.scalars(query):
        candidates.setdefault(row.symbol, row)
    return candidates


def _observation(value: object, *, nonnegative: bool = False) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
        return None if nonnegative and value < 0 else value
    return None


def _sort_candidates(candidates: list[Candidate], field: SortField, direction: SortDirection) -> None:
    if field == "symbol":
        candidates.sort(key=lambda item: item.target.symbol, reverse=direction == "desc")
        return

    def sort_key(item: Candidate) -> tuple[bool, float, str]:
        value = getattr(item, field)
        # Keep missing observations last in both directions and break ties by symbol.
        if value is None:
            return True, 0, item.target.symbol
        if isinstance(value, datetime):
            value = value.timestamp()
        return False, -value if direction == "desc" else value, item.target.symbol

    candidates.sort(key=sort_key)


def create_screening_router(require_reader, require_writer) -> APIRouter:
    router = APIRouter()

    @router.get("/screening", dependencies=[Depends(require_reader)])
    def screen(
        db: DB, market: Market, q: ScreeningQuery = "",
        min_price: Annotated[Price | None, Query()] = None, max_price: Annotated[Price | None, Query()] = None,
        sort: SortField = "symbol", direction: SortDirection = "asc",
        asset_type: Literal["etf"] | None = None, observed_within: RecordRange | None = None,
    ) -> MobileResponse[Screening]:
        if min_price is not None and max_price is not None and min_price > max_price:
            raise api_error(400, "mobile_price_range_invalid", "价格区间无效。")
        if q:
            universe = catalog_symbols(q, market)
            saved = _watchlist_records(db, market, list(universe)) if universe else {}
        else:
            saved = _watchlist_records(db, market)
            universe = {symbol: WatchlistSymbol.model_validate(stock, from_attributes=True) for symbol, stock in saved.items()}
        types = {symbol: stock.asset_type for symbol, stock in saved.items() if stock.asset_type in ("stock", "etf")}
        if asset_type is not None:
            universe = {symbol: target for symbol, target in universe.items() if types.get(symbol) == asset_type}
        currency = _CURRENCIES[market]
        rows = md_quote_rows(list(universe), market) if universe else []
        quotes = {row["symbol"]: row for row in rows if row["market"] == market}
        now = datetime.now(UTC)
        cutoff = now - _RECORD_RANGES[observed_within] if observed_within else None
        items = []
        missing = 0
        for symbol, target in universe.items():
            quote = quotes.get(symbol, {})
            price = _observation(quote.get("current_price"))
            record_time = quote_observed_at(quote) if price is not None else None
            if price is None:
                missing += 1
            if cutoff is not None and (record_time is None or not cutoff <= record_time <= now):
                continue
            if min_price is not None and (price is None or price < min_price):
                continue
            if max_price is not None and (price is None or price > max_price):
                continue
            items.append(Candidate(
                target=target, asset_type=types.get(symbol), price=price,
                change_pct=_observation(quote.get("change_pct")),
                turnover=_observation(quote.get("turnover"), nonnegative=True),
                volume_ratio=_observation(quote.get("volume_ratio"), nonnegative=True),
                quote_date=quote.get("quote_date"), observed_at=record_time, source=quote.get("source") or None, saved=symbol in saved,
            ))
        _sort_candidates(items, sort, direction)
        return MobileResponse(data=Screening(
            candidates=items, currency=currency, missing_quotes=missing, sort=sort, direction=direction,
            asset_type=asset_type, observed_within=observed_within,
        ))

    @router.post("/symbols/watchlist/add", dependencies=[Depends(require_writer)])
    def add(body: WatchlistTarget, db: DB) -> MobileResponse[WatchlistSymbol]:
        current = select(Stock).where(Stock.market == body.market, Stock.symbol == body.symbol).order_by(Stock.id).limit(1)
        row = db.scalar(current)
        if row is None:
            candidate = catalog_symbols(body.symbol, body.market).get(body.symbol)
            if candidate is None:
                raise api_error(404, "mobile_symbol_not_found", "此标的不可用。")
            row = add_stock(db, name=candidate.name, market=body.market, symbol=candidate.symbol) or db.scalar(current)
            if row is None:
                raise api_error(409, "mobile_watchlist_changed", "自选已变更，请重试。")
        return MobileResponse(data=WatchlistSymbol.model_validate(row, from_attributes=True))

    @router.post("/symbols/watchlist/remove", dependencies=[Depends(require_writer)])
    def remove(body: WatchlistTarget, db: DB) -> MobileResponse[bool]:
        matches = (Stock.market == body.market, Stock.symbol == body.symbol)
        targets = select(Stock.id).where(*matches).correlate(None)
        legacy_holdings = select(Stock.id).where(
            *matches, or_(Stock.quantity != 0, Stock.cost_price != 0, Stock.invested_amount != 0),
        ).correlate(None).exists()
        references = [
            select(model.id).where(model.stock_id.in_(targets)).exists()
            for model in (Position, StockAgent, PriceAlertRule, PriceAlertHit)
        ]
        # Keep the dependency checks and deletion in the same SQLite write statement.
        deleted = db.execute(
            delete(Stock).where(*matches, ~legacy_holdings, *(~reference for reference in references))
            .execution_options(synchronize_session=False),
        )
        if deleted.rowcount == 0 and db.scalar(targets.limit(1)) is not None:
            raise api_error(409, "mobile_watchlist_in_use", "此自选仍关联持仓、盯盘规则或 Agent。")
        db.commit()
        return MobileResponse(data=True)

    return router
