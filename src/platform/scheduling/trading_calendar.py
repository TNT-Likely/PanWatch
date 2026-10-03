"""Bounded exchange calendars shared by display and automatic execution.

Published annual exchange data is bundled with the application. Runtime warmup
only materializes the recent window (30 days back, 90 days ahead), without any
network requests or full-history decoding. Unpublished weekdays are unknown and
cannot authorize automatic work. Historical K-line queries remain independent.
"""
from __future__ import annotations

import logging
import time as clock
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from src.platform.scheduling.exchange_calendar_data import HOLIDAYS, EARLY_CLOSES

logger = logging.getLogger(__name__)
_RECENT_OPEN_DAYS: dict[str, frozenset[date]] = {}
_WARMED_ON: date | None = None
_FALLBACK_TZ = "Asia/Shanghai"


def reset_cache() -> None:
    global _WARMED_ON
    _RECENT_OPEN_DAYS.clear()
    _WARMED_ON = None


def _to_market_code(market):
    from src.platform.marketdata.models import MarketCode
    if isinstance(market, MarketCode):
        return market
    try:
        return MarketCode(str(market).strip().upper())
    except ValueError:
        return None


def _market_tz(code) -> ZoneInfo:
    from src.platform.marketdata.models import MARKETS
    definition = MARKETS.get(code)
    return definition.get_tz() if definition else ZoneInfo(_FALLBACK_TZ)


def _now_in_market_tz(code) -> datetime:
    return datetime.now(_market_tz(code))


def _resolve_date(code, d: date | datetime | None) -> date:
    if d is None:
        return _now_in_market_tz(code).date()
    if isinstance(d, datetime):
        return d.astimezone(_market_tz(code)).date() if d.tzinfo else d.date()
    return d


def calendar_known(market, d: date | datetime | None = None) -> bool:
    code = _to_market_code(market)
    target = _resolve_date(code, d)
    return code is not None and (target.weekday() >= 5 or (code.value, target.year) in HOLIDAYS)


def is_trading_day(market, d: date | datetime | None = None) -> bool:
    code = _to_market_code(market)
    target = _resolve_date(code, d)
    if code is None or target.weekday() >= 5:
        return False
    holidays = HOLIDAYS.get((code.value, target.year))
    return holidays is not None and target not in holidays


def refresh_blocking() -> bool:
    """Warm a bounded local window; never contact a calendar data provider."""
    global _WARMED_ON
    today = _now_in_market_tz(_to_market_code("CN")).date()
    if _WARMED_ON == today:
        return bool(_RECENT_OPEN_DAYS)
    started = clock.monotonic()
    start, end = today - timedelta(days=30), today + timedelta(days=90)
    window = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    for code in ("CN", "HK", "US"):
        _RECENT_OPEN_DAYS[code] = frozenset(day for day in window if is_trading_day(code, day))
    _WARMED_ON = today
    logger.info("[交易日历] 本地近期日历已加载: %s ~ %s, %.1f ms (无需网络)",
                start, end, (clock.monotonic() - started) * 1000)
    return any(_RECENT_OPEN_DAYS.values())


async def refresh() -> bool:
    return refresh_blocking()


def trading_sessions(market, d: date | datetime | None = None) -> list:
    from src.platform.marketdata.models import MARKETS, TradingSession
    code = _to_market_code(market)
    target = _resolve_date(code, d)
    if code not in MARKETS or not is_trading_day(code, target):
        return []
    sessions = MARKETS[code].sessions
    close = EARLY_CLOSES.get((code.value, target))
    if close is None:
        return sessions
    return [TradingSession(s.start, min(s.end, close)) for s in sessions if s.start < close]


def market_status(market, dt: datetime | None = None) -> str:
    code = _to_market_code(market)
    if code is None:
        return "unknown"
    now = dt.astimezone(_market_tz(code)) if dt is not None else _now_in_market_tz(code)
    if not calendar_known(code, now):
        return "unknown"
    sessions = trading_sessions(code, now)
    if not sessions:
        return "closed"
    current = now.time()
    if any(s.start <= current <= s.end for s in sessions):
        return "trading"
    if current < sessions[0].start:
        return "pre_market"
    if current > sessions[-1].end:
        return "after_hours"
    return "break"


def eligible_markets(markets=None, dt: datetime | None = None, *, trading_hours_only: bool = False) -> list[str]:
    from src.platform.marketdata.models import MARKETS
    return [code.value for raw in (MARKETS if markets is None else markets)
            if (code := _to_market_code(raw)) is not None
            and (market_status(code, dt) == "trading" if trading_hours_only else is_trading_day(code, dt))]


def any_market_trading_day(d: date | datetime | None = None) -> bool:
    from src.platform.marketdata.models import MARKETS
    return any(is_trading_day(code, d) for code in MARKETS)


def next_eligible_time(market, dt: datetime, *, trading_hours_only: bool = False) -> datetime | None:
    """Find a confirmed boundary, allowing preview to jump over closed periods."""
    code = _to_market_code(market)
    if code is None:
        return None
    local = dt.astimezone(_market_tz(code))
    for offset in range(370):
        day = local.date() + timedelta(days=offset)
        if not is_trading_day(code, day):
            continue
        earliest = local if offset == 0 else datetime.combine(day, time.min, _market_tz(code))
        if not trading_hours_only:
            return earliest
        for session in trading_sessions(code, day):
            opening = datetime.combine(day, session.start, _market_tz(code))
            closing = datetime.combine(day, session.end, _market_tz(code))
            if earliest <= closing:
                return max(earliest, opening)
    return None


def local_day_bounds(market, d: date | datetime | None = None) -> tuple[datetime, datetime]:
    code = _to_market_code(market)
    day = _resolve_date(code, d)
    tz = _market_tz(code)
    return (datetime.combine(day, time.min, tz).astimezone(timezone.utc),
            datetime.combine(day + timedelta(days=1), time.min, tz).astimezone(timezone.utc))


def upcoming_calendar(market, *, days: int = 14, start_date: date | None = None) -> dict:
    code = _to_market_code(market)
    now = _now_in_market_tz(code)
    rows = []
    for offset in range(days):
        day = (start_date or now.date()) + timedelta(days=offset)
        known = calendar_known(code, day)
        opened = is_trading_day(code, day)
        reason = ("unpublished" if not known else "weekend" if day.weekday() >= 5
                  else "holiday" if not opened else "early_close" if (code.value, day) in EARLY_CLOSES
                  else "trading")
        rows.append({"date": day.isoformat(), "is_trading_day": opened if known else None,
                     "reason": reason, "sessions": [f"{s.start:%H:%M}-{s.end:%H:%M}" for s in trading_sessions(code, day)],
                     "session_times": [{"open": datetime.combine(day, s.start, _market_tz(code)).isoformat(),
                                        "close": datetime.combine(day, s.end, _market_tz(code)).isoformat()}
                                       for s in trading_sessions(code, day)]})
    next_open = None
    for offset in range(370):
        day = now.date() + timedelta(days=offset)
        openings = [datetime.combine(day, session.start, _market_tz(code))
                    for session in trading_sessions(code, day)]
        next_open = next((opening for opening in openings if opening > now), None)
        if next_open is not None:
            break
    return {"market": code.value, "timezone": str(_market_tz(code)),
            "local_date": now.date().isoformat(), "local_time": now.strftime("%H:%M"), "status": market_status(code, now), "source": "exchange",
            "covered_years": sorted(year for (m, year) in HOLIDAYS if m == code.value),
            "next_open": next_open.isoformat() if next_open else None, "days": rows}
