from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.platform.scheduling import trading_calendar as tc
from src.platform.marketdata.models import MARKETS, MarketCode
from src.platform.marketdata.quote_display import daily_quote_fields
from src.modules.market.api.stocks import get_market_status
from src.modules.market.api.quotes import _quote_to_response


@pytest.fixture(autouse=True)
def offline_calendar():
    tc.reset_cache()
    yield
    tc.reset_cache()


def freeze(monkeypatch, value):
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: value.astimezone(tc._market_tz(code)))


def test_national_day_status_works_before_calendar_warmup(monkeypatch):
    freeze(monkeypatch, datetime(2026, 10, 1, 14, 33, tzinfo=ZoneInfo("Asia/Shanghai")))
    result = {row["code"]: row for row in get_market_status()}
    assert result["CN"]["status"] == result["HK"]["status"] == "closed"
    assert result["CN"]["is_trading"] is False
    assert result["US"]["status"] == "pre_market"


@pytest.mark.parametrize("hour,minute,expected", [
    (8, 0, "pre_market"), (10, 0, "trading"), (12, 0, "break"), (16, 0, "after_hours"),
])
def test_reopening_session_states(monkeypatch, hour, minute, expected):
    freeze(monkeypatch, datetime(2026, 10, 8, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")))
    cn = get_market_status()[0]
    assert cn["status"] == expected
    assert cn["is_trading"] is (expected == "trading")


@pytest.mark.parametrize("market,day,hour", [("HK", "2026-12-24", 13), ("US", "2026-11-27", 14)])
def test_half_days_close_early(market, day, hour):
    md = MARKETS[MarketCode(market)]
    local = datetime.combine(date.fromisoformat(day), datetime.min.time(), md.get_tz()).replace(hour=hour)
    assert tc.market_status(market, local) == "after_hours"
    assert md.is_trading_time(local) is False


@pytest.mark.parametrize("day,hour,expected", [
    ("2026-10-01", 14, "closed"), ("2026-10-03", 10, "closed"),
    ("2026-10-08", 8, "pre_market"), ("2026-10-08", 10, "current"),
    ("2026-10-08", 12, "current"), ("2026-10-08", 16, "current"),
])
def test_daily_changes_follow_exchange_day(monkeypatch, day, hour, expected):
    freeze(monkeypatch, datetime.fromisoformat(day).replace(hour=hour, tzinfo=ZoneInfo("Asia/Shanghai")))
    raw = {"current_price": 10.5, "prev_close": 10, "change_pct": 5, "change_amount": .5}
    result = _quote_to_response("600519", MarketCode.CN, raw)
    assert result["current_price"] == 10.5
    assert result["daily_move_status"] == expected
    assert result["change_pct"] == (5 if expected == "current" else None)
    assert raw["change_pct"] == 5  # Raw data for analysis/automation remains intact.


def test_previous_session_quote_is_not_todays_change(monkeypatch):
    freeze(monkeypatch, datetime(2026, 10, 8, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    result = daily_quote_fields("CN", {"current_price": 10, "change_pct": 5, "quote_date": "2026-09-30"})
    assert result["daily_move_status"] == "stale"
    assert result["change_pct"] is None
    assert result["quote_date"] == "2026-09-30"


def test_us_daily_change_uses_new_york_date(monkeypatch):
    # Shanghai is already Saturday; New York is still trading on Friday.
    freeze(monkeypatch, datetime(2026, 10, 3, 1, tzinfo=ZoneInfo("Asia/Shanghai")))
    fields = daily_quote_fields("US", {"current_price": 10, "change_pct": 2, "quote_date": "2026-10-02"})
    assert fields["daily_move_status"] == "current"
    assert fields["change_pct"] == 2


def test_portfolio_only_sums_today_for_open_markets(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from src.platform.persistence.database import Base
    from src.platform.persistence.models import Account, Stock, Position
    from src.modules.portfolio.api import accounts as api

    freeze(monkeypatch, datetime(2026, 10, 1, 22, tzinfo=ZoneInfo("Asia/Shanghai")))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            account = Account(name="Mixed", available_funds=0, enabled=True)
            cn = Stock(symbol="600519", name="CN", market="CN")
            us = Stock(symbol="AAPL", name="US", market="US")
            db.add_all([account, cn, us])
            db.flush()
            db.add_all([
                Position(account_id=account.id, stock_id=cn.id, cost_price=8, quantity=100),
                Position(account_id=account.id, stock_id=us.id, cost_price=30, quantity=2),
            ])
            db.commit()
            monkeypatch.setattr(api, "get_usd_cny_rate", lambda: 7)
            monkeypatch.setattr(api, "_fetch_quotes_for_stocks", lambda _: {
                "600519": {"market": "CN", "current_price": 11, "prev_close": 10, "change_pct": 10},
                "AAPL": {"market": "US", "current_price": 50, "prev_close": 40, "change_pct": 25},
            })
            result = api.get_portfolio_summary(db=db)
            rows = {p["market"]: p for p in result["accounts"][0]["positions"]}
            assert rows["CN"]["current_price"] == 11
            assert rows["CN"]["pnl"] == 300
            assert rows["CN"]["change_pct"] is None
            assert rows["CN"]["daily_pnl"] == 0
            assert rows["US"]["daily_pnl"] == 140
            assert result["total"]["total_daily_pnl"] == 140
            assert result["quotes"]["600519"]["change_pct"] is None

            def forbidden():
                raise AssertionError("Metadata-only refresh must not call external FX sources")

            monkeypatch.setattr(api, "get_hkd_cny_rate", forbidden)
            monkeypatch.setattr(api, "get_usd_cny_rate", forbidden)
            monkeypatch.setattr(api, "_fetch_quotes_for_stocks", lambda _: forbidden())
            metadata = api.get_portfolio_summary(include_quotes=False, refresh_exchange_rates=False, db=db)
            assert metadata["accounts"][0]["positions"][1]["quantity"] == 2
    finally:
        engine.dispose()
