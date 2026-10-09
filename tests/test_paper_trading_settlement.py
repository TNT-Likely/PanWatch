"""Cash-account settlement with production fills, persistence and API reads.

Time and quotes are controlled; these are integration tests, not live-market fills.
"""
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.paper_trading import paper_settlement as settlement
from src.modules.paper_trading import paper_trading_engine as engine_module
from src.modules.paper_trading.api import paper_trading as api
from src.modules.paper_trading.paper_trading_notifier import _format_daily_summary
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import (
    PaperTradingAccount, PaperTradingPosition, PaperTradingSettlement,
    PaperTradingTrade, StrategySignalRun,
)
from src.platform.scheduling import trading_calendar as calendar


def local(market, day, hour=10):
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(
        tzinfo=ZoneInfo(settlement.MARKET_TIMEZONES[market]))


@pytest.mark.parametrize("market,day,expected", [
    ("CN", "2026-10-09", "2026-10-12"),
    ("CN", "2026-09-30", "2026-10-08"),
    ("HK", "2026-10-09", "2026-10-13"),
    ("HK", "2026-02-13", "2026-02-23"),
    ("HK", "2026-12-22", "2026-12-28"),
    ("HK", "2026-12-30", None),
    ("US", "2026-10-09", "2026-10-13"),
    ("US", "2026-11-10", "2026-11-12"),
    ("US", "2026-11-25", "2026-11-27"),
    ("US", "2026-12-23", "2026-12-24"),
    ("US", "2026-04-02", "2026-04-06"),
    ("US", "2026-07-02", "2026-07-06"),
    ("US", "2026-12-31", None),
])
def test_confirmed_settlement_days(market, day, expected):
    due = settlement.settlement_date(market, local(market, day))
    assert (due.isoformat() if due else None) == expected


def test_dates_use_market_timezone_and_naive_utc():
    # Both are Friday locally, despite a different UTC date.
    assert settlement.settlement_date("US", datetime(2026, 10, 10, 1)) == date(2026, 10, 13)
    assert settlement.settlement_date("HK", datetime(2026, 10, 8, 17)) == date(2026, 10, 13)
    assert settlement.settlement_date("XX", datetime.now()) is None
    assert settlement.settlement_date("CN", None) is None


@pytest.fixture
def storage(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(engine_module, "SessionLocal", factory)
    yield factory
    engine.dispose()


def freeze(monkeypatch, now):
    monkeypatch.setattr(engine_module, "_utc_now", lambda: now.astimezone(timezone.utc))
    monkeypatch.setattr(settlement, "utc_now", lambda: now.astimezone(timezone.utc))
    monkeypatch.setattr(calendar, "_now_in_market_tz", lambda code: now.astimezone(calendar._market_tz(code)))


def seed_position(factory, market, now):
    buy_cost = -engine_module.COST_MODEL.fill("buy", 10, 400).cash_delta
    with factory() as db:
        account = PaperTradingAccount(initial_capital=5000, current_capital=5000 - buy_cost,
            enabled=True, market_allocations={m: float(m == market) for m in ("CN", "HK", "US")})
        pos = PaperTradingPosition(stock_symbol="OLD", stock_market=market, quantity=400,
            entry_price=10, current_price=10, status="open", highest_price=10,
            opened_at=(now - timedelta(days=1) if market == "CN" else now).astimezone(timezone.utc))
        db.add_all([account, pos])
        db.commit()
        return pos.id


@pytest.mark.parametrize("market", ["HK", "US"])
def test_cash_account_same_day_sale_blocks_reuse_until_due(monkeypatch, storage, market):
    now = local(market, "2026-10-09")
    freeze(monkeypatch, now)
    ident = seed_position(storage, market, now)
    engine = engine_module.PaperTradingEngine()
    with storage() as db:
        account = db.query(PaperTradingAccount).one()
        engine._close_position(db, account, db.get(PaperTradingPosition, ident), 12, "manual")
        db.add(StrategySignalRun(stock_symbol="NEW", stock_market=market, status="active",
            strategy_code="trend_follow", snapshot_date="2026-10-09", action="buy",
            rank_score=90, entry_low=10, entry_high=10))
        db.commit()
        cash_after_sale, pnl_after_sale = account.current_capital, account.total_pnl
        row = db.query(PaperTradingSettlement).one()
        assert row.amount == engine_module.COST_MODEL.fill("sell", 12, 400).cash_delta
        assert row.settlement_date == date(2026, 10, 13)
        funds = api._account_summary(db, account, market)
        assert funds["cash_balance"] > 5000
        assert funds["buying_power"] < 1000
        assert funds["buying_power"] == funds["settled_cash"]
        assert funds["unsettled_cash"] == round(row.amount, 2)
        assert engine_module.market_available_cash(db, account, market) < 1000
    monkeypatch.setattr(engine, "_fetch_quotes_map", lambda pairs: {(market, "NEW"): {"current_price": 10}})
    assert engine._scan_sync()["opened"] == 0
    # The US stock market is open on Columbus Day, but cash is still blocked.
    freeze(monkeypatch, local(market, "2026-10-12"))
    assert engine._scan_sync()["opened"] == 0
    # A new engine/session simulates restart and catches up the persisted ledger.
    freeze(monkeypatch, local(market, "2026-10-13"))
    resumed = engine_module.PaperTradingEngine()
    monkeypatch.setattr(resumed, "_fetch_quotes_map", lambda pairs: {(market, "NEW"): {"current_price": 10}})
    result = resumed._scan_sync()
    assert result["opened"] == 1 and result["settled"] == 1
    with storage() as db:
        account = db.query(PaperTradingAccount).one()
        pos = db.query(PaperTradingPosition).filter_by(status="open").one()
        outlay = engine_module.position_buy_cost(pos)
        assert account.current_capital == pytest.approx(cash_after_sale - outlay)
        assert account.total_pnl == pnl_after_sale
        assert db.query(PaperTradingSettlement).one().status == "settled"
        assert pos.settlement_date == (date(2026, 10, 15) if market == "HK" else date(2026, 10, 14))
        assert engine_module.sell_block_reason(pos) is None
    assert resumed._scan_sync()["settled"] == 0


def test_cn_proceeds_reused_without_unlocking_new_shares(monkeypatch, storage):
    now = local("CN", "2026-10-09")
    freeze(monkeypatch, now)
    ident = seed_position(storage, "CN", now)
    engine = engine_module.PaperTradingEngine()
    with storage() as db:
        account = db.query(PaperTradingAccount).one()
        old_settled_cash = account.current_capital
        engine._close_position(db, account, db.get(PaperTradingPosition, ident), 12, "manual")
        db.add(StrategySignalRun(stock_symbol="NEW", stock_market="CN", status="active",
            strategy_code="trend_follow", snapshot_date="2026-10-09", action="buy",
            rank_score=90, entry_low=10, entry_high=10))
        db.commit()
    monkeypatch.setattr(engine, "_fetch_quotes_map", lambda pairs: {("CN", "NEW"): {"current_price": 10}})
    assert engine._scan_sync()["opened"] == 1
    with storage() as db:
        account = db.query(PaperTradingAccount).one()
        pos = db.query(PaperTradingPosition).filter_by(status="open").one()
        row = db.query(PaperTradingSettlement).one()
        assert row.remaining_amount == pytest.approx(row.amount - engine_module.position_buy_cost(pos))
        funds = api._account_summary(db, account, "CN")
        assert funds["buying_power"] == round(account.current_capital, 2)
        assert funds["settled_cash"] == round(old_settled_cash, 2)
        assert engine_module.sell_block_reason(pos) == "paper_trading_t1_locked"


def add_sale(db, market="US", amount=100, due=date(2026, 10, 13)):
    trade = PaperTradingTrade(stock_symbol="TEST", stock_market=market, quantity=100,
        entry_price=1, exit_price=1, pnl=0, pnl_pct=0, closed_at=local(market, "2026-10-09"))
    db.add(trade)
    db.flush()
    row = PaperTradingSettlement(trade_id=trade.id, stock_market=market, amount=amount,
        remaining_amount=amount, traded_at=trade.closed_at, settlement_date=due, status="pending")
    db.add(row)
    db.flush()
    return row


def test_cn_partial_and_full_consumption_preserves_cash_conservation(storage):
    now = local("CN", "2026-10-09")
    with storage() as db:
        row = add_sale(db, "CN", due=date(2026, 10, 12))
        settlement.consume_cn_proceeds(db, 40, now=now)
        funds = settlement.cash_summary(db, 1060, "CN", now=now)
        assert row.remaining_amount == 60
        assert funds["settled_cash"] == 1000
        settlement.consume_cn_proceeds(db, 90, now=now)
        funds = settlement.cash_summary(db, 970, "CN", now=now)
        assert row.remaining_amount == 0
        assert funds["settled_cash"] == funds["buying_power"] == 970
        assert funds["pending_settlements"] == []


def test_paused_settlement_is_idempotent_and_reads_do_not_write(monkeypatch, storage):
    freeze(monkeypatch, local("US", "2026-10-13", 0))
    with storage() as db:
        db.add(PaperTradingAccount(initial_capital=1000, current_capital=1100, enabled=False))
        row = add_sale(db)
        db.commit()
        # Due availability is correct before the scheduler writes the state.
        assert settlement.cash_summary(db, 1100, "US")["buying_power"] == 1100
        assert row.status == "pending" and not db.dirty
    engine = engine_module.PaperTradingEngine()
    assert engine._scan_sync() == {"status": "disabled", "settled": 1}
    assert engine._scan_sync() == {"status": "disabled", "settled": 0}
    with storage() as db:
        assert db.query(PaperTradingAccount).one().current_capital == 1100
        assert db.query(PaperTradingSettlement).one().settled_at is not None


def test_unknown_calendar_stays_blocked_then_recovers(monkeypatch, storage):
    now = local("US", "2026-10-09")
    with storage() as db:
        row = add_sale(db, due=None)
        monkeypatch.setattr(settlement, "settlement_date", lambda *args: None)
        funds = settlement.cash_summary(db, 1100, "US", now=now)
        assert funds["buying_power"] == 1000
        assert funds["pending_settlements"][0]["status"] == "unknown"
        assert settlement.settle_due(db, now=now) == 0
        monkeypatch.setattr(settlement, "settlement_date", lambda *args: date(2026, 10, 13))
        assert settlement.settle_due(db, now=local("US", "2026-10-13")) == 1
        assert row.status == "settled"


def test_unrounded_limit_does_not_spend_displayed_cents(storage):
    with storage() as db:
        assert settlement.cash_summary(db, 1005.505, "US")["buying_power"] == 1005.5
        cash = 1005.506
        assert settlement.cash_summary(db, cash, "US")["buying_power"] == 1005.51
        assert settlement.buying_power(db, cash, "US") == cash
        assert engine_module._compute_quantity(rank_score=90, market_budget=10000,
            price=10, available_cash=settlement.buying_power(db, cash, "US"),
            cost_model=engine_module.COST_MODEL) == 0


def test_reallocation_cannot_spend_invested_cash_or_cn_pending_proceeds(monkeypatch, storage):
    freeze(monkeypatch, local("US", "2026-10-09"))
    with storage() as db:
        pos = PaperTradingPosition(stock_symbol="OLD", stock_market="CN", quantity=900,
            entry_price=10, current_price=10, status="open")
        remaining = 10000 - engine_module.position_buy_cost(pos)
        account = PaperTradingAccount(initial_capital=10000, current_capital=remaining,
            market_allocations={"CN": .1, "HK": 0, "US": .9})
        db.add_all([pos, account]);db.commit()
        # US has a 9,000 paper allocation, but most cash is invested in CN.
        assert engine_module.market_available_cash(db, account, "US") == remaining
        assert api._account_summary(db, account, None)["buying_power"] == round(remaining, 2)
        # Reassigning CN sale proceeds to US cannot turn them into settled cash.
        add_sale(db, "CN", amount=900, due=date(2026, 10, 12));db.commit()
        assert engine_module.market_available_cash(db, account, "US") == pytest.approx(remaining - 900)
        us = api._account_summary(db, account, "US")
        assert us["buying_power"] == us["settled_cash"] == round(remaining - 900, 2)
        assert api._account_summary(db, account, None)["buying_power"] == round(remaining - 900, 2)


def test_unallocated_reserve_and_notifications_share_the_buying_limit(storage):
    from src.modules.paper_trading.paper_trading_notifier import _account_funds
    with storage() as db:
        account = PaperTradingAccount(initial_capital=10000, current_capital=10000,
            market_allocations={"CN": .2, "HK": .1, "US": .1})
        db.add(account);db.commit()
        summary = api._account_summary(db, account, None)
        assert summary["cash_balance"] == summary["settled_cash"] == 10000
        assert summary["buying_power"] == 4000
        assert _account_funds(db, account)["buying_power"] == 4000


def test_one_scan_cannot_spend_shared_cash_in_two_markets(monkeypatch, storage):
    freeze(monkeypatch, local("CN", "2026-10-09"))
    with storage() as db:
        old = PaperTradingPosition(stock_symbol="OLD", stock_market="US", quantity=350,
            entry_price=10, current_price=10, highest_price=10, status="open")
        account = PaperTradingAccount(initial_capital=5000,
            current_capital=5000 - engine_module.position_buy_cost(old), enabled=True,
            market_allocations={"CN": .5, "HK": .5, "US": 0})
        db.add_all([old, account])
        for market in ["CN", "HK"]:
            db.add(StrategySignalRun(stock_symbol="NEW", stock_market=market, status="active",
                strategy_code="trend_follow", action="buy", rank_score=90,
                entry_low=10, entry_high=10, snapshot_date="2026-10-09"))
        db.commit()
    engine = engine_module.PaperTradingEngine()
    monkeypatch.setattr(engine, "_fetch_quotes_map", lambda pairs: {
        (m, "NEW"): {"current_price": 10} for m in ["CN", "HK"]})
    assert engine._scan_sync()["opened"] == 1
    with storage() as db:
        assert db.query(PaperTradingAccount).one().current_capital >= 0
        assert db.query(PaperTradingPosition).filter_by(stock_symbol="NEW").count() == 1


def test_trade_and_cash_roll_back_with_failed_settlement(monkeypatch, storage):
    now = local("US", "2026-10-09")
    freeze(monkeypatch, now)
    ident = seed_position(storage, "US", now)
    with storage() as db:
        account = db.query(PaperTradingAccount).one()
        original = account.current_capital
        engine_module.PaperTradingEngine()._close_position(db, account, db.get(PaperTradingPosition, ident), 12, "manual")
        db.rollback()
        assert db.get(PaperTradingPosition, ident).status == "open"
        assert db.query(PaperTradingTrade).count() == db.query(PaperTradingSettlement).count() == 0
        assert db.query(PaperTradingAccount).one().current_capital == original


def test_sale_fees_exceeding_proceeds_do_not_create_buying_power(monkeypatch, storage):
    now = local("US", "2026-10-09")
    freeze(monkeypatch, now)
    with storage() as db:
        pos = PaperTradingPosition(stock_symbol="PENNY", stock_market="US", quantity=100,
            entry_price=.01, current_price=.01, status="open", opened_at=now)
        account = PaperTradingAccount(initial_capital=1000,
            current_capital=1000 - engine_module.position_buy_cost(pos),
            market_allocations={"CN": 0, "HK": 0, "US": 1})
        db.add_all([pos, account]);db.commit()
        before = account.current_capital
        engine_module.PaperTradingEngine()._close_position(db, account, pos, .01, "manual")
        db.commit()
        row = db.query(PaperTradingSettlement).one()
        assert row.amount < 0 and row.remaining_amount == 0
        assert account.current_capital < before
        funds = api._account_summary(db, account, "US")
        assert funds["buying_power"] == round(account.current_capital, 2)
        assert funds["unsettled_cash"] == 0 and funds["pending_settlements"] == []


def test_unique_ledger_and_reset(storage):
    with storage() as db:
        db.add(PaperTradingAccount(initial_capital=1000, current_capital=1100))
        row = add_sale(db)
        db.commit()
        db.add(PaperTradingSettlement(trade_id=row.trade_id, stock_market="US", amount=100,
            remaining_amount=100, traded_at=row.traded_at, status="pending"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    assert engine_module.PaperTradingEngine().reset_account()["ok"]
    with storage() as db:
        assert db.query(PaperTradingSettlement).count() == db.query(PaperTradingTrade).count() == 0
        assert db.query(PaperTradingAccount).one().current_capital == 1000


def test_concurrent_manual_requests_close_once(monkeypatch, storage):
    now = local("US", "2026-10-09")
    freeze(monkeypatch, now)
    ident = seed_position(storage, "US", now)
    quoting, release = Event(), Event()
    calls = []
    def quote(*args):
        calls.append(args)
        quoting.set()
        assert release.wait(2)
        return [{"current_price": 12}]
    monkeypatch.setattr(engine_module, "md_quote_rows", quote)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(engine_module.PaperTradingEngine().close_position_manual, ident)
        assert quoting.wait(1)
        second = pool.submit(engine_module.PaperTradingEngine().close_position_manual, ident)
        try:
            with pytest.raises(TimeoutError):
                second.result(timeout=.05)
        finally:
            release.set()
        assert first.result(timeout=2)["ok"]
        assert not second.result(timeout=2)["ok"]
    assert len(calls) == 1
    with storage() as db:
        assert db.query(PaperTradingTrade).count() == db.query(PaperTradingSettlement).count() == 1


def test_api_market_filter_legacy_and_refresh(monkeypatch, storage):
    freeze(monkeypatch, local("US", "2026-10-09"))
    with storage() as db:
        db.add(PaperTradingAccount(initial_capital=1000, current_capital=1000,
            market_allocations={"CN": .5, "HK": .3, "US": .2}))
        add_sale(db, "CN", due=date(2026, 10, 12))
        add_sale(db, "US")
        db.add(PaperTradingTrade(stock_symbol="LEGACY", stock_market="US", quantity=100,
            entry_price=1, exit_price=1, pnl=0, pnl_pct=0))
        db.commit()
    app = FastAPI()
    app.include_router(api.router, prefix="/paper-trading")
    def db_override():
        with storage() as db:
            yield db
    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        us = client.get("/paper-trading/account?market=US").json()
        assert us["buying_power"] == 100 and us["cash_balance"] == 200
        assert [r["market"] for r in us["pending_settlements"]] == ["US"]
        all_markets = client.get("/paper-trading/account").json()
        assert all_markets["buying_power"] == 900
        assert all_markets["settled_cash"] == 800
        trades = client.get("/paper-trading/trades?market=US").json()["items"]
        legacy = next(t for t in trades if t["stock_symbol"] == "LEGACY")
        assert legacy["settlement_status"] == "legacy" and legacy["settlement_amount"] is None
        current = next(t for t in trades if t["stock_symbol"] == "TEST")
        assert current["settlement_date"] == "2026-10-13" and current["settlement_status"] == "pending"
        freeze(monkeypatch, local("US", "2026-10-13"))
        assert client.get("/paper-trading/account?market=US").json()["buying_power"] == 200


def test_migration_preserves_opening_funds_and_old_trades(tmp_path):
    from src.platform.persistence.migrations import _m136_paper_trading_settlement
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE paper_trading_positions (id INTEGER PRIMARY KEY, opened_at DATETIME)"))
        conn.execute(text("CREATE TABLE paper_trading_accounts (id INTEGER PRIMARY KEY, current_capital FLOAT)"))
        conn.execute(text("CREATE TABLE paper_trading_trades (id INTEGER PRIMARY KEY, pnl FLOAT)"))
        conn.execute(text("INSERT INTO paper_trading_accounts VALUES (1, 9876.54)"))
        conn.execute(text("INSERT INTO paper_trading_trades VALUES (1, 123.45)"))
        _m136_paper_trading_settlement(conn)
        _m136_paper_trading_settlement(conn)
        assert "settlement_date" in {r[1] for r in conn.execute(text("PRAGMA table_info(paper_trading_positions)"))}
        assert conn.execute(text("SELECT current_capital FROM paper_trading_accounts")).scalar() == 9876.54
        assert conn.execute(text("SELECT pnl FROM paper_trading_trades")).scalar() == 123.45
        assert conn.execute(text("SELECT count(*) FROM paper_trading_settlements")).scalar() == 0
    engine.dispose()


@pytest.mark.parametrize("english", [False, True])
def test_notification_distinguishes_cash_from_buying_power(english):
    account = SimpleNamespace(current_capital=1100)
    _, body = _format_daily_summary([], [], account, english=english,
        funds={"buying_power": 1000, "unsettled_cash": 100})
    assert ("Buying power: 1,000.00" if english else "购买力: 1,000.00") in body
    assert ("Cash balance: 1,100.00" if english else "现金余额: 1,100.00") in body
