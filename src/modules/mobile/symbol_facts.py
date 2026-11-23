"""Qualified observations for the instance's watchlist and market catalog."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from math import isfinite
from statistics import fmean
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path
from marketdata.types import Bar
from pydantic import BaseModel, FiniteFloat, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.mobile.schemas import MobileResponse, WatchlistSymbol
from src.modules.mobile.screening import catalog_symbols
from src.platform.marketdata.collectors.kline_collector import _calculate_macd, _calculate_rsi
from src.platform.marketdata.marketdata_client import get_market_data, md_quote_rows
from src.platform.marketdata.quote_display import assistant_quote_fields
from src.platform.persistence.database import get_db
from src.platform.persistence.models import Stock
from src.web.errors import api_error

DB = Annotated[Session, Depends(get_db)]
Interval = Literal["1d", "1w", "1m"]
Market = Literal["CN", "HK", "US"]
_CURRENCIES = {"CN": "CNY", "HK": "HKD", "US": "USD"}
_HISTORY_SESSIONS = 360


class Point(BaseModel):
    date: date
    open: FiniteFloat
    high: FiniteFloat
    low: FiniteFloat
    close: FiniteFloat
    volume: FiniteFloat | None = None

    @field_validator("volume", mode="before")
    @classmethod
    def known_volume(cls, value):
        return value if _finite(value) and value > 0 else None


class QuoteSnapshot(BaseModel):
    price: FiniteFloat
    change_pct: FiniteFloat | None
    source: str | None
    date: date | None
    source_timestamp: datetime | None
    freshness: Literal["fresh", "delayed", "stale", "unknown"]


class DailyTechnicals(BaseModel):
    as_of: date
    macd_hist: FiniteFloat | None
    macd_history: list[FiniteFloat]
    rsi6: FiniteFloat | None
    volume_ratio_5d: FiniteFloat | None


class SymbolFacts(BaseModel):
    target: WatchlistSymbol
    currency: Literal["CNY", "HKD", "USD"]
    quote: QuoteSnapshot | None
    interval: Interval
    bars_source: str | None
    bars: list[Point]
    sma20_daily: FiniteFloat | None
    technicals_daily: DailyTechnicals | None


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _daily_technicals(bars: list[Bar]) -> DailyTechnicals | None:
    if not bars:
        return None
    closes = [bar.close for bar in bars]
    macd = _calculate_macd(closes)
    volumes = [bar.volume for bar in bars[-5:]]
    mean_volume = fmean(volumes) if len(volumes) == 5 and all(_finite(volume) for volume in volumes) else None
    return DailyTechnicals(
        as_of=bars[-1].date,
        macd_hist=macd[2][-1] if macd else None,
        macd_history=macd[2][-8:] if macd else [],
        rsi6=_calculate_rsi(closes, period=6),
        volume_ratio_5d=volumes[-1] / mean_volume if mean_volume else None,
    )


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


def _quote(target: WatchlistSymbol) -> QuoteSnapshot | None:
    rows = md_quote_rows([target.symbol], target.market)
    raw = next((row for row in rows if row["symbol"] == target.symbol and row["market"] == target.market), None)
    return quote_snapshot(target.market, raw)


def load_symbol_facts(db: Session, market: Market, symbol: str, interval: Interval, aggregate_klines) -> SymbolFacts:
    stock = db.scalar(select(Stock).where(Stock.market == market, Stock.symbol == symbol).order_by(Stock.id).limit(1))
    target = (
        WatchlistSymbol.model_validate(stock, from_attributes=True)
        if stock is not None else catalog_symbols(symbol, market).get(symbol)
    )
    if target is None:
        raise api_error(404, "mobile_symbol_not_found", "此标的不可用。")
    quote = _quote(target)
    bars, vendor = get_market_data().klines_with_vendor(
        target.symbol, market=market, days=_HISTORY_SESSIONS,
    )
    # Some vendors use zero for missing volume. Preserve that uncertainty through period sums.
    daily = sorted(
        (
            replace(bar, volume=bar.volume if _finite(bar.volume) and bar.volume > 0 else float("nan"))
            for bar in bars if all(_finite(value) for value in (bar.open, bar.high, bar.low, bar.close))
        ),
        key=lambda bar: bar.date,
    )
    points = [Point.model_validate(bar, from_attributes=True) for bar in aggregate_klines(daily, interval)[-60:]]
    average = fmean(bar.close for bar in daily[-20:]) if len(daily) >= 20 else None
    return SymbolFacts(
        target=target, currency=_CURRENCIES[market],
        quote=quote, interval=interval, bars_source=vendor or None, bars=points, sma20_daily=average,
        technicals_daily=_daily_technicals(daily),
    )


def create_symbol_facts_router(require_device, aggregate_klines: Callable[[list, str], list]) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_device)])

    @router.get("/symbols/{market}/{symbol}")
    def view(
        market: Market, symbol: Annotated[str, Path(min_length=1, max_length=80)],
        db: DB, interval: Interval = "1d",
    ) -> MobileResponse[SymbolFacts]:
        return MobileResponse(data=load_symbol_facts(db, market, symbol, interval, aggregate_klines))

    return router
