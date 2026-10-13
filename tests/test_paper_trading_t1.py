"""A-share settlement guards using the real paper engine and isolated storage."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.paper_trading import paper_trading_engine as module
from src.modules.paper_trading.api import paper_trading as api
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import PaperTradingAccount, PaperTradingPosition, PaperTradingTrade
from src.platform.scheduling import trading_calendar as calendar

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 9, 10, tzinfo=SHANGHAI)


@pytest.fixture
def storage(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as db:
        db.add(PaperTradingAccount(initial_capital=100000, current_capital=98999, enabled=True))
        db.commit()
    yield factory
    engine.dispose()


def freeze(monkeypatch, now=NOW):
    monkeypatch.setattr(module, "_utc_now", lambda: now.astimezone(timezone.utc))
    monkeypatch.setattr(calendar, "_now_in_market_tz", lambda code: now.astimezone(calendar._market_tz(code)))


def position(storage, *, market="CN", opened=NOW, **kwargs):
    with storage() as db:
        pos = PaperTradingPosition(stock_symbol="600519", stock_market=market,
            quantity=100, entry_price=10, current_price=10, highest_price=10,
            status="open", opened_at=opened.astimezone(timezone.utc).replace(tzinfo=None), **kwargs)
        db.add(pos)
        db.commit()
        return pos.id


def test_legacy_positions_are_not_automatically_transferred(monkeypatch, storage):
    freeze(monkeypatch)
    ident = position(storage, stop_loss=9, target_price=12)
    engine = module.PaperTradingEngine()
    monkeypatch.setattr(engine, "_fetch_quotes_map", lambda pairs: {("CN", "600519"): {"current_price": 8}})
    assert engine._scan_sync()["closed"] == 0
    freeze(monkeypatch, NOW + timedelta(days=3))
    assert engine._scan_sync()["closed"] == 0
    with storage() as db:
        assert db.get(PaperTradingPosition, ident).status == "open"
        assert db.query(PaperTradingTrade).count() == 0


def test_manual_same_day_rejects_before_quote_and_without_writes(monkeypatch, storage):
    freeze(monkeypatch)
    ident = position(storage)
    fetch = Mock(side_effect=AssertionError("locked positions must not fetch a quote"))
    monkeypatch.setattr(module, "md_quote_rows", fetch)
    result = module.PaperTradingEngine().close_position_manual(ident)
    assert result["ok"] is False
    assert result["error_code"] == "paper_trading_t1_locked"
    fetch.assert_not_called()
    with storage() as db:
        assert db.get(PaperTradingPosition, ident).status == "open"
        assert db.query(PaperTradingTrade).count() == 0
        assert db.query(PaperTradingAccount).one().current_capital == 98999


def test_shared_fill_guard_rejects_same_day(monkeypatch, storage):
    freeze(monkeypatch)
    ident = position(storage)
    with storage() as db:
        with pytest.raises(ValueError, match="paper_trading_t1_locked"):
            module.PaperTradingEngine()._close_position(db, db.query(PaperTradingAccount).one(),
                db.get(PaperTradingPosition, ident), 12, "manual")
        assert db.query(PaperTradingTrade).count() == 0
        assert db.get(PaperTradingPosition, ident).status == "open"


def test_qlib_buy_persists_t1_lock_across_engine_reload(monkeypatch, storage):
    from tests.qlib_fixture import paper_decision
    instant = NOW.replace(hour=9, minute=31)
    freeze(monkeypatch, instant)
    paper_decision(storage, asof="2026-10-08", symbols=("SH600519",))
    engine = module.PaperTradingEngine()
    monkeypatch.setattr(engine, "_fetch_quotes_map", lambda pairs: {("CN", "600519"): {"current_price": 10, "open_price": 10, "prev_close": 10, "volume": 100000, "quote_date": "2026-10-09"}})
    assert engine._scan_sync()["opened"] == 1
    with storage() as db:
        pos = db.query(PaperTradingPosition).one()
        assert pos.research_meta["model_id"]
        capital, ident = db.query(PaperTradingAccount).one().current_capital, pos.id
    freeze(monkeypatch, instant + timedelta(hours=1))
    result = module.PaperTradingEngine().close_position_manual(ident)
    assert result["error_code"] == "paper_trading_t1_locked"
    with storage() as db:
        assert db.query(PaperTradingTrade).count() == 0
        assert db.query(PaperTradingAccount).one().current_capital == capital


@pytest.mark.parametrize("opened,now,expected", [
    # Different UTC dates, same Shanghai date.
    ("2026-10-08T23:30:00+00:00", "2026-10-09T10:00:00+08:00", False),
    # Less than 24 hours later, but a new trading date.
    ("2026-10-08T14:55:00+08:00", "2026-10-09T09:35:00+08:00", True),
    # Future purchase timestamps remain locked.
    ("2026-10-12T10:00:00+08:00", "2026-10-09T10:00:00+08:00", False),
])
def test_manual_uses_shanghai_date_not_elapsed_hours(monkeypatch, storage, opened, now, expected):
    freeze(monkeypatch, datetime.fromisoformat(now))
    ident = position(storage, opened=datetime.fromisoformat(opened))
    monkeypatch.setattr(module, "md_quote_rows", lambda *args: [{"current_price": 12}])
    assert module.PaperTradingEngine().close_position_manual(ident)["ok"] is expected
    with storage() as db:
        assert db.query(PaperTradingTrade).count() == int(expected)


@pytest.mark.parametrize("market,now", [("HK", NOW), ("US", datetime(2026, 10, 9, 22, tzinfo=SHANGHAI))])
def test_non_cn_manual_can_sell_same_day(monkeypatch, storage, market, now):
    freeze(monkeypatch, now)
    ident = position(storage, market=market, opened=now)
    monkeypatch.setattr(module, "md_quote_rows", lambda *args: [{"current_price": 12}])
    assert module.PaperTradingEngine().close_position_manual(ident)["ok"] is True
    with storage() as db:
        assert db.query(PaperTradingTrade).one().stock_market == market


def test_api_exposes_settlement_lock_and_specific_error(monkeypatch, storage):
    freeze(monkeypatch)
    ident = position(storage)
    app = FastAPI()
    app.include_router(api.router)
    def database():
        with storage() as db:
            yield db
    app.dependency_overrides[get_db] = database
    client = TestClient(app)
    row = client.get("/positions").json()[0]
    assert row["sellable_quantity"] == 0
    assert row["sell_block_reason"] == "paper_trading_t1_locked"
    response = client.post(f"/positions/{ident}/close")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "paper_trading_t1_locked"
    freeze(monkeypatch, NOW + timedelta(days=3))
    row = client.get("/positions").json()[0]
    assert row["sellable_quantity"] == 100
    assert row["sell_block_reason"] is None


def test_missing_buy_time_fails_closed(monkeypatch, storage):
    freeze(monkeypatch)
    pos = PaperTradingPosition(stock_market="CN", entry_price=10, quantity=100, status="open")
    assert module.sell_block_reason(pos) == "paper_trading_open_time_missing"
    assert api._position_response(pos)["sellable_quantity"] == 0
