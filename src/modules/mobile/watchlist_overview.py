"""Bounded watchlist observations, separate from the editable watchlist."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from math import isfinite
from typing import Literal

from marketdata.cache import TTLCache
from pydantic import BaseModel, FiniteFloat
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from src.modules.mobile.schemas import WatchlistSymbol
from src.modules.mobile.symbol_facts import QuoteSnapshot, quote_snapshot
from src.platform.marketdata.marketdata_client import get_market_data, md_quote_rows
from src.platform.persistence.models import PriceAlertRule, Stock


_CURRENCIES = {"CN": "CNY", "HK": "HKD", "US": "USD"}
_HISTORY_SESSIONS = 14
PAGE_SIZE = 6
_trend_cache = TTLCache(default_ttl_sec=600, max_size=512)


class WatchlistObservation(BaseModel):
    target: WatchlistSymbol
    currency: Literal["CNY", "HKD", "USD"] | None
    quote: QuoteSnapshot | None
    enabled_alerts: int | None


class WatchlistOverview(BaseModel):
    items: list[WatchlistObservation]
    total: int
    next_offset: int | None
    enabled_alerts: int | None


class Close(BaseModel):
    date: date
    price: FiniteFloat


class WatchlistTrend(BaseModel):
    target: WatchlistSymbol
    points: list[Close]
    source: str | None


def _watchlist(db: Session) -> list[WatchlistSymbol]:
    rows = db.execute(select(Stock.symbol, Stock.name, Stock.market).order_by(Stock.sort_order, Stock.id))
    by_key = {}
    for symbol, name, market in rows:
        by_key.setdefault((market, symbol), WatchlistSymbol(symbol=symbol, name=name, market=market))
    return list(by_key.values())


def _quotes(market: str, symbols: list[str]) -> dict[str, QuoteSnapshot | None]:
    try:
        rows = md_quote_rows(symbols, market)
    except Exception:
        return {}
    return {
        row["symbol"]: quote_snapshot(market, row)
        for row in rows if row.get("market") == market and row.get("symbol") in symbols
    }


def load_watchlist_overview(db: Session, *, offset: int, limit: int, read_alerts: bool) -> WatchlistOverview:
    targets = _watchlist(db)
    page = targets[offset:offset + limit]
    by_market = defaultdict(list)
    for target in page:
        if target.market in _CURRENCIES:
            by_market[target.market].append(target.symbol)
    quotes = {market: _quotes(market, symbols) for market, symbols in by_market.items()}
    counts = {}
    enabled_alerts = None
    if read_alerts:
        enabled = select(PriceAlertRule.id).join(Stock, Stock.id == PriceAlertRule.stock_id).where(PriceAlertRule.enabled)
        enabled_alerts = db.scalar(select(func.count()).select_from(enabled.subquery()))
        if page:
            keys = [(target.market, target.symbol) for target in page]
            rows = db.execute(
                select(Stock.market, Stock.symbol, func.count(PriceAlertRule.id))
                .join(PriceAlertRule, Stock.id == PriceAlertRule.stock_id)
                .where(PriceAlertRule.enabled, tuple_(Stock.market, Stock.symbol).in_(keys))
                .group_by(Stock.market, Stock.symbol)
            )
            counts = {(market, symbol): count for market, symbol, count in rows}
    return WatchlistOverview(
        items=[
            WatchlistObservation(
                target=target, currency=_CURRENCIES.get(target.market),
                quote=quotes.get(target.market, {}).get(target.symbol),
                enabled_alerts=counts.get((target.market, target.symbol), 0) if read_alerts else None,
            )
            for target in page
        ],
        total=len(targets),
        next_offset=offset + limit if offset + limit < len(targets) else None,
        enabled_alerts=enabled_alerts,
    )


def _trend(target: WatchlistSymbol) -> WatchlistTrend:
    key = f"{target.market}:{target.symbol}"
    cached = _trend_cache.get(key)
    if cached is None:
        closes = {}
        vendor = None
        if target.market in _CURRENCIES:
            try:
                bars, vendor = get_market_data().klines_with_vendor(
                    target.symbol, market=target.market, days=_HISTORY_SESSIONS,
                )
            except Exception:
                bars = []
            for bar in bars:
                if not isfinite(bar.close) or bar.close <= 0:
                    continue
                try:
                    closes[date.fromisoformat(bar.date)] = bar.close
                except (TypeError, ValueError):
                    continue
        dates = sorted(closes)[-_HISTORY_SESSIONS:]
        cached = ([Close(date=day, price=closes[day]) for day in dates], (vendor or None) if dates else None)
        _trend_cache.set(key, cached, ttl_sec=None if dates else 30)
    points, source = cached
    return WatchlistTrend(target=target, points=points, source=source)


def load_watchlist_trends(db: Session, *, offset: int, limit: int) -> list[WatchlistTrend]:
    page = _watchlist(db)[offset:offset + limit]
    if not page:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(page))) as pool:
        return list(pool.map(_trend, page))
