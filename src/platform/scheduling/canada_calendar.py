"""Published TMX 2026 schedule, shared by TSX and TSX Venture.

Source: https://www.tsx.com/en/trading/calendars-and-trading-hours/calendar
Unknown years stay unavailable rather than silently adopting US holidays.
"""
from datetime import date, time

_CLOSED_2026 = frozenset(date.fromisoformat(day) for day in (
    "2026-01-01", "2026-02-16", "2026-04-03", "2026-05-18",
    "2026-07-01", "2026-08-03", "2026-09-07", "2026-10-12",
    "2026-12-25", "2026-12-28",
))
HOLIDAYS = {(market, 2026): _CLOSED_2026 for market in ("TSX", "TSXV")}
EARLY_CLOSES = {(market, date(2026, 12, 24)): time(13) for market in ("TSX", "TSXV")}
