"""Which exchange session a provider quote can substantiate in a research report."""
from datetime import date, datetime, timedelta
import math

from src.platform.marketdata.models import MARKETS, IndexData, MarketCode, StockData
from src.platform.scheduling.trading_calendar import calendar_known, exchange_session_boundary, market_local_date, trading_sessions


INTRADAY_MAX_AGE = timedelta(minutes=10)


def research_date(agent_name, market, instant):
    if agent_name == "intraday_monitor":
        return market_local_date(market, instant)
    boundary = exchange_session_boundary(market, instant, next_open=agent_name == "premarket_outlook")
    return boundary.date() if boundary else None


def session_quote_validity(agent_name: str, market: MarketCode, quote: StockData | IndexData | dict | None,
                           observed: datetime) -> tuple[timedelta | None, str | None]:
    """Return the usable research lifetime or the reason the quote was rejected."""
    if quote is None:
        return None, "quote_missing"
    if isinstance(quote, dict):
        price = quote.get("current_price", quote.get("current"))
        source_time, source_day = quote.get("source_timestamp"), quote.get("quote_date")
        try:
            if isinstance(source_time, str):
                source_time = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
            if isinstance(source_day, str):
                source_day = date.fromisoformat(source_day)
        except ValueError:
            return None, "quote_invalid_time"
    else:
        price = getattr(quote, "current_price", None)
        source_time = getattr(quote, "source_timestamp", None)
        source_day = getattr(quote, "quote_date", None)
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return None, "quote_invalid_price"
    zone = MARKETS[market].get_tz()
    local = observed.astimezone(zone)
    if source_time is not None:
        if not isinstance(source_time, datetime) or source_time.tzinfo is None or source_time > observed:
            return None, "quote_invalid_time"
        source_time = source_time.astimezone(zone)
        if source_day is not None and source_time.date() != source_day:
            return None, "quote_invalid_time"
        source_day = source_time.date()
    if not isinstance(source_day, date):
        return None, "quote_time_unknown"
    if not calendar_known(market, local):
        return None, "quote_calendar_unknown"
    sessions = trading_sessions(market, local)
    if agent_name == "intraday_monitor":
        active = next((s for s in sessions if s.start <= local.time() <= s.end), None)
        if active is None:
            return None, "quote_market_closed"
        if source_day != local.date():
            return None, "quote_wrong_session"
        if source_time is None:
            return None, "quote_time_unknown"
        age = observed - source_time
        if source_time.time() < active.start or age >= INTRADAY_MAX_AGE:
            return None, "quote_delayed"
        return min(timedelta(minutes=1), INTRADAY_MAX_AGE - age), None

    # A timestamped same-day pre-open price is an indicative quote. A date
    # alone could still refer to the previous close before the market opens.
    if (agent_name == "premarket_outlook" and sessions and local.time() < sessions[0].start
            and source_time is not None and source_day == local.date()):
        remaining = timedelta(minutes=15) - (observed - source_time)
        if remaining <= timedelta(0):
            return None, "quote_delayed"
        opening = datetime.combine(local.date(), sessions[0].start, zone)
        return min(remaining, opening - local), None
    if agent_name == "premarket_outlook" and research_date(agent_name, market, observed) is None:
        return None, "quote_calendar_unknown"
    close = exchange_session_boundary(market, observed)
    if close is None:
        return None, "quote_calendar_unknown"
    if source_day != close.date():
        return None, "quote_wrong_session"
    if source_time is not None and source_time < close:
        return None, "quote_before_close"
    return timedelta(minutes=15), None
