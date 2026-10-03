"""Forward outcomes count actual, completed daily bars rather than calendar days."""
from datetime import date
from math import isfinite

from src.platform.scheduling import trading_calendar as calendar


def completed_outcome_bar(klines, base_day: date, horizon: int, market) -> tuple[date, float] | None:
    code = calendar._to_market_code(market)
    if code is None:
        return None
    now = calendar._now_in_market_tz(code)
    sessions = calendar.trading_sessions(code, now)
    today_complete = bool(sessions and now.time() > sessions[-1].end)
    rows = {}
    for bar in klines or []:
        try:
            day = date.fromisoformat(str(bar.date)[:10].replace("/", "-"))
            close = float(bar.close)
        except (AttributeError, TypeError, ValueError):
            continue
        if day <= base_day or day > now.date() or (day == now.date() and not today_complete):
            continue
        if not isfinite(close) or close <= 0:
            continue
        if calendar.calendar_known(code, day) and not calendar.is_trading_day(code, day):
            continue
        rows[day] = close
    future = sorted(rows.items())
    index = max(1, int(horizon)) - 1
    return future[index] if index < len(future) else None
