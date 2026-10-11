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


def quote_date_is_current(market: str, quote: dict) -> bool:
    """Reject a known stale date; providers without quote dates retain compatibility."""
    quote_date = quote.get("quote_date")
    if not quote_date:
        return True
    code = calendar._to_market_code(market)
    return code is not None and str(quote_date)[:10] == calendar._now_in_market_tz(code).date().isoformat()


def assistant_quote_fields(market: str, quote: dict, now: datetime | None = None) -> dict:
    """Strict source-time semantics; fetch time never proves a quote is live."""
    from datetime import UTC

    observed = now or datetime.now(UTC)
    code = calendar._to_market_code(market)
    local_now = observed.astimezone(calendar._market_tz(code))
    status = calendar.market_status(code, local_now)
    quote_date = str(quote.get("quote_date") or "") or None
    source_time = quote.get("source_timestamp")
    parsed = None
    if source_time:
        try:
            parsed = datetime.fromisoformat(str(source_time).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = None  # An unknown vendor timezone must not be inferred.
        except ValueError:
            pass
    if not quote_date and parsed:
        quote_date = parsed.astimezone(calendar._market_tz(code)).date().isoformat()
    freshness = "unknown"
    basis = "unknown"
    if quote_date:
        basis = "quote_date"
        if quote_date < local_now.date().isoformat():
            freshness = "stale"
        elif quote_date == local_now.date().isoformat():
            freshness = "delayed"  # A date alone cannot establish intraday timeliness.
    if parsed:
        age = (observed - parsed).total_seconds()
        source_date = parsed.astimezone(calendar._market_tz(code)).date().isoformat()
        basis = "source_timestamp"
        if age < -300 or (quote_date and source_date != quote_date):
            freshness = "unknown"
        elif source_date < local_now.date().isoformat():
            freshness = "stale"
        elif status == "unknown":
            freshness = "unknown"
        elif status in ("closed", "pre_market", "after_hours", "break"):
            freshness = "delayed"
        else:
            freshness = "fresh" if age <= 600 else "delayed" if age <= 3600 else "stale"
    # A provider-declared delay wins even when its source timestamp is recent.
    if quote.get("delayed") is True and freshness == "fresh":
        freshness = "delayed"
    current = freshness == "fresh"
    return {
        "quote_date": quote_date,
        "source_timestamp": parsed.isoformat() if parsed else None,
        "market_status": status,
        "freshness": freshness,
        "freshness_basis": basis,
        "is_realtime": current,
        "quote_semantics": "provider_snapshot",
        "bar_close_confirmed": False,
        "change_pct": quote.get("change_pct") if current else None,
        "change_amount": quote.get("change_amount") if current else None,
        "source_change_pct": quote.get("change_pct"),
        "source_change_amount": quote.get("change_amount"),
    }


def assistant_quote_summary(market: str, quote: dict) -> str:
    """Render the same source/currency/freshness contract for every assistant host."""
    symbol = quote.get("symbol") or "?"
    name = quote.get("name") or symbol
    currency = quote.get("currency") or "币种未知"
    security_type = quote.get("security_type") or "证券类型未知"
    source = quote.get("source") or "来源未知"
    as_of = quote.get("source_timestamp") or quote.get("quote_date") or "时间未知"
    delayed = {True: "是", False: "否"}.get(quote.get("delayed"), "未知")
    summary = (
        f"{name}（{market}:{symbol}）来源报价 {quote.get('current_price')} {currency}，"
        f"证券类型 {security_type}；来源 {source}，数据时点 {as_of}；"
        f"市场 {quote['market_status']}，时效 {quote['freshness']}，供应商延迟 {delayed}。"
    )
    summary += f"来源涨跌幅 {quote.get('source_change_pct')}%，成交量 {quote.get('volume')}。"
    if quote.get("observed_at"):
        summary += f"采集时间 {quote['observed_at']}。"
    if quote['is_realtime']:
        summary += f"涨跌幅 {quote.get('change_pct')}%。"
    else:
        summary += "不能视为当前实时行情。"
    return summary
