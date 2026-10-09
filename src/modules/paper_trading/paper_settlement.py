"""Date-granularity cash settlement for the paper cash account.

Cash balance and equity recognize fills immediately. Outstanding sale proceeds
are tracked separately; settling a row never credits cash for a second time.
CN may reuse proceeds, while HK/US purchases require settled, unreserved cash.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.platform.persistence.models import PaperTradingSettlement, PaperTradingTrade
from src.platform.scheduling import trading_calendar as calendar
from src.platform.scheduling.exchange_calendar_data import EARLY_CLOSES

SETTLEMENT_DAYS = {"CN": 1, "HK": 2, "US": 1}
MARKET_TIMEZONES = {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}
# DTC 23034-25: banking holidays with no settlement, despite stock-market trading.
# https://www.dtcc.com/-/media/Files/pdf/2025/10/15/23034-25.pdf
US_NON_SETTLEMENT_DAYS = {2026: frozenset((date(2026, 10, 12), date(2026, 11, 11)))}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def market_date(market: str, stamp: datetime) -> date:
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(ZoneInfo(MARKET_TIMEZONES[market])).date()


def settlement_date(market: str, traded_at: datetime | None) -> date | None:
    """Count confirmed settlement days, never assuming unpublished weekdays."""
    if traded_at is None or market not in SETTLEMENT_DAYS:
        return None
    day = market_date(market, traded_at)
    remaining = SETTLEMENT_DAYS[market]
    for _ in range(370):
        day += timedelta(days=1)
        if not calendar.calendar_known(market, day):
            return None
        if market == "US" and day.year not in US_NON_SETTLEMENT_DAYS:
            return None
        if not calendar.is_trading_day(market, day):
            continue
        # HKEX CT/075/25: the three half-day sessions are non-settlement days.
        if market == "HK" and (market, day) in EARLY_CLOSES:
            continue
        if market == "US" and day in US_NON_SETTLEMENT_DAYS[day.year]:
            continue
        remaining -= 1
        if remaining == 0:
            return day
    return None


def row_due_date(row: PaperTradingSettlement) -> date | None:
    return row.settlement_date or settlement_date(row.stock_market, row.traded_at)


def settlement_status(market: str, due: date | None, *, now: datetime | None = None) -> str:
    if due is None:
        return "unknown"
    return "settled" if market_date(market, now or utc_now()) >= due else "pending"


def pending_rows(db: Session, market: str | None = None, *, now: datetime | None = None) -> list[PaperTradingSettlement]:
    query = db.query(PaperTradingSettlement).filter(PaperTradingSettlement.status == "pending")
    if market:
        query = query.filter(PaperTradingSettlement.stock_market == market)
    stamp = now or utc_now()
    return [row for row in query.order_by(PaperTradingSettlement.traded_at, PaperTradingSettlement.id).all()
            if row.remaining_amount > 0 and settlement_status(row.stock_market, row_due_date(row), now=stamp) != "settled"]


def buying_power(db: Session, cash: float, market: str, *, now: datetime | None = None,
                 all_markets: bool = False) -> float:
    """Unrounded execution limit; displayed cents must never increase a budget."""
    blocked = sum(row.remaining_amount for row in pending_rows(db, None if all_markets else market, now=now)
                  if market != "CN" or row.stock_market != "CN")
    return max(0.0, cash - blocked)


def cash_summary(db: Session, cash: float, market: str | None = None, *, now: datetime | None = None) -> dict:
    now = now or utc_now()
    rows = pending_rows(db, market, now=now)
    unsettled = sum(row.remaining_amount for row in rows)
    blocked = sum(row.remaining_amount for row in rows if row.stock_market != "CN")
    return {
        "cash_balance": round(cash, 2),
        "settled_cash": round(max(0.0, cash - unsettled), 2),
        "unsettled_cash": round(unsettled, 2),
        "buying_power": round(max(0.0, cash - blocked), 2),
        "funding_policy": "sale_proceeds" if market == "CN" else "settled_cash" if market else "mixed",
        "settlement_cycles": dict(SETTLEMENT_DAYS),
        "pending_settlements": [{
            "trade_id": row.trade_id, "market": row.stock_market,
            "amount": round(row.amount, 2), "remaining_amount": round(row.remaining_amount, 2),
            "settlement_date": row_due_date(row).isoformat() if row_due_date(row) else None,
            "status": settlement_status(row.stock_market, row_due_date(row), now=now),
        } for row in rows],
    }


def settle_due(db: Session, *, now: datetime | None = None) -> int:
    """Materialize due states in the caller's transaction; do not change cash."""
    stamp = now or utc_now()
    count = 0
    for row in db.query(PaperTradingSettlement).filter(PaperTradingSettlement.status == "pending").all():
        due = row_due_date(row)
        row.settlement_date = due
        if settlement_status(row.stock_market, due, now=stamp) == "settled":
            row.status = "settled"
            row.settled_at = stamp
            count += 1
    return count


def consume_cn_proceeds(db: Session, outlay: float, *, now: datetime | None = None) -> None:
    """CN uses outstanding proceeds first, preserving the remaining settled cash."""
    remaining = outlay
    for row in pending_rows(db, "CN", now=now):
        used = min(remaining, row.remaining_amount)
        row.remaining_amount = max(0.0, row.remaining_amount - used)
        remaining -= used
        if remaining <= 0:
            break


def record_sale(db: Session, trade: PaperTradingTrade, proceeds: float) -> None:
    db.flush()  # The trade and its uniquely keyed settlement share one transaction.
    db.add(PaperTradingSettlement(
        trade_id=trade.id, stock_market=trade.stock_market, amount=proceeds,
        remaining_amount=max(0.0, proceeds), traded_at=trade.closed_at,
        settlement_date=settlement_date(trade.stock_market, trade.closed_at), status="pending",
    ))
