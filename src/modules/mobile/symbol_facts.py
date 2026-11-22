"""Qualified market observations for symbols already in the instance's universe."""

from datetime import date, datetime
from math import isfinite
from statistics import fmean
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, FiniteFloat
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.market.api.klines import _aggregate_klines
from src.modules.mobile.schemas import MobileResponse, WatchlistSymbol
from src.platform.marketdata.marketdata_client import get_market_data, md_quote_rows
from src.platform.marketdata.quote_display import assistant_quote_fields
from src.platform.persistence.database import get_db
from src.platform.persistence.models import Stock
from src.web.errors import api_error

DB = Annotated[Session, Depends(get_db)]
Interval = Literal["1d", "1w", "1m"]
Market = Literal["CN", "HK", "US"]
_CURRENCIES = {"CN": "CNY", "HK": "HKD", "US": "USD"}
_INTERVAL_DAYS = {"1d": 90, "1w": 180, "1m": 360}


class Point(BaseModel):
    date: date
    open: FiniteFloat
    high: FiniteFloat
    low: FiniteFloat
    close: FiniteFloat


class QuoteSnapshot(BaseModel):
    price: FiniteFloat
    change_pct: FiniteFloat | None
    source: str | None
    date: date | None
    source_timestamp: datetime | None
    freshness: Literal["fresh", "delayed", "stale", "unknown"]


class SymbolFacts(BaseModel):
    target: WatchlistSymbol
    currency: Literal["CNY", "HKD", "USD"]
    quote: QuoteSnapshot | None
    interval: Interval
    bars_source: str | None
    bars: list[Point]
    sma20_daily: FiniteFloat | None


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def quote_snapshot(market: str, raw: dict | None) -> QuoteSnapshot | None:
    if raw is None or not _finite(raw.get("current_price")):
        return None
    fields = assistant_quote_fields(market, raw)
    change = fields["change_pct"]
    return QuoteSnapshot(
        price=raw["current_price"], change_pct=change if _finite(change) else None,
        source=raw.get("source") or None, date=fields["quote_date"],
        source_timestamp=fields["source_timestamp"], freshness=fields["freshness"],
    )


def _quote(stock: Stock) -> QuoteSnapshot | None:
    rows = md_quote_rows([stock.symbol], stock.market)
    raw = next((row for row in rows if row["symbol"] == stock.symbol and row["market"] == stock.market), None)
    return quote_snapshot(stock.market, raw)


def create_symbol_facts_router(require_device) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_device)])

    @router.get("/symbols/{market}/{symbol}")
    def view(
        market: Market, symbol: Annotated[str, Path(min_length=1, max_length=80)],
        db: DB, interval: Interval = "1d",
    ) -> MobileResponse[SymbolFacts]:
        stock = db.scalar(select(Stock).where(Stock.market == market, Stock.symbol == symbol).order_by(Stock.id).limit(1))
        if stock is None:
            raise api_error(404, "mobile_symbol_not_found", "此标的不可用。")
        quote = _quote(stock)
        bars, vendor = get_market_data().klines_with_vendor(
            stock.symbol, market=market, days=_INTERVAL_DAYS[interval],
        )
        daily = sorted((bar for bar in bars if all(_finite(value) for value in (bar.open, bar.high, bar.low, bar.close))),
                       key=lambda bar: bar.date)
        points = [Point.model_validate(bar, from_attributes=True) for bar in _aggregate_klines(daily, interval)[-60:]]
        average = fmean(bar.close for bar in daily[-20:]) if len(daily) >= 20 else None
        return MobileResponse(data=SymbolFacts(
            target=WatchlistSymbol.model_validate(stock, from_attributes=True), currency=_CURRENCIES[market],
            quote=quote, interval=interval, bars_source=vendor or None, bars=points, sma20_daily=average,
        ))

    return router
