"""Daily quote semantics for HTTP presentation; preserve raw vendor data."""

from datetime import datetime

from src.platform.scheduling import trading_calendar as calendar


def daily_quote_fields(market: str, quote: dict | None, now: datetime | None = None) -> dict:
    code = calendar._to_market_code(market)
    local_now = now.astimezone(calendar._market_tz(code)) if now else calendar._now_in_market_tz(code)
    status = calendar.market_status(code, local_now)
    quote_date = (quote or {}).get("quote_date")
    if status in ("closed", "pre_market", "unknown"):
        daily_status = status
    elif not quote or quote.get("current_price") is None:
        daily_status = "missing"
    elif quote_date and quote_date != local_now.date().isoformat():
        daily_status = "stale"
    else:
        daily_status = "current"
    available = daily_status == "current"
    return {
        "change_pct": quote.get("change_pct") if available else None,
        "change_amount": quote.get("change_amount") if available else None,
        "daily_move_status": daily_status,
        "quote_date": quote_date,
    }
