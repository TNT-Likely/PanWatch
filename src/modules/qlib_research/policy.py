"""Pure top/bottom dropout plan matching the pinned Qlib preset (n_drop=topk).

Keep winners, drop the lowest-ranked eligible holdings, allocate 90% of cash
equally to entrants. Target weights describe the intended pool, not fill promises.
"""

import math
import re
from datetime import date, timedelta
from src.platform.scheduling.trading_calendar import calendar_known, is_trading_day


POLICY = "topk_dropout_daily_next_open_90pct_cash"


def paper_security_supported(row):
    """Defense in depth: optional flags cannot grant unsupported securities."""
    return (
        row.get("market", "CN") == "CN"
        and bool(re.fullmatch(r"(?:SH60|SZ00)\d{4}", row.get("symbol", "")))
        and row.get("paper_eligible", True)
        and row.get("price_basis", "raw_execution") == "raw_execution"
    )


def execution_date(asof):
    day = date.fromisoformat(asof)
    for _ in range(20):
        day += timedelta(days=1)
        if not calendar_known("CN", day):
            raise ValueError("qlib_execution_calendar_unknown")
        if is_trading_day("CN", day):
            return day.isoformat()
    raise ValueError("qlib_execution_calendar_unknown")


def dropout_plan(predictions, held, eligible, topk):
    score = {row["symbol"]: row["raw_score"] for row in predictions}
    def ordered(codes):
        return sorted(codes, key=lambda symbol: (-score.get(symbol, -math.inf), symbol))
    last = ordered(held)
    today = ordered(set(score) - set(last))
    today = [symbol for symbol in today if symbol in eligible][:max(0, 2 * topk - len(last))]
    combined = ordered(set(last) | set(today))
    bottom = [symbol for symbol in reversed(combined) if symbol in eligible][:topk]
    sell = [symbol for symbol in last if symbol in bottom]
    buy = today[:max(0, len(sell) + topk - len(last))]
    return sell, buy


def lot_quantity(budget, price, available, costs, lot=100):
    if not math.isfinite(price) or price <= 0 or budget <= 0 or available <= 0:
        return 0
    quantity = max(0, int(budget / price) // lot * lot)
    # Costs can reduce size; never force the minimum lot beyond the target.
    while quantity > 0 and -costs.fill("buy", price, quantity).cash_delta > available:
        quantity -= lot
    return quantity
