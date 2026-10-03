"""Market-local paper notifications must not include holiday markets/trades."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from src.platform.persistence.models import PaperTradingAccount, PaperTradingPosition, PaperTradingTrade
from tests.test_trading_execution_guards import memory_db, freeze


def test_us_summary_uses_new_york_trade_date_with_both_utc_bounds(monkeypatch, memory_db):
    from src.modules.paper_trading import paper_trading_notifier as notifier
    instant = datetime(2026, 10, 2, 4, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    freeze(monkeypatch, instant)
    monkeypatch.setattr(notifier, "SessionLocal", memory_db)
    monkeypatch.setattr(notifier, "_is_mode_enabled", lambda *_args: True)
    monkeypatch.setattr(notifier, "_report_is_english", lambda: True)
    manager = type("Notifier", (), {"notify": AsyncMock()})()
    monkeypatch.setattr(notifier, "_build_notifier", lambda: manager)
    with memory_db() as db:
        db.add(PaperTradingAccount(enabled=True, initial_capital=100_000, current_capital=100_000,
                                  market_allocations={"CN": .5, "US": .5}))
        for symbol, market, closed in [
            ("VALID", "US", datetime(2026, 10, 2, 0, 10)),
            ("EARLY", "US", datetime(2026, 10, 1, 3, 59)),
            ("FUTURE", "US", datetime(2026, 10, 2, 4, 1)),
            ("CLOSED_CN", "CN", datetime(2026, 10, 1, 10)),
        ]:
            db.add(PaperTradingTrade(stock_symbol=symbol, stock_market=market, stock_name=symbol,
                   quantity=100, entry_price=10, exit_price=11, pnl=100, pnl_pct=10, closed_at=closed))
        db.commit()
    asyncio.run(notifier.send_daily_summary(markets=["CN", "US"]))
    manager.notify.assert_awaited_once()
    title, body = manager.notify.call_args.args
    assert "US" in title and "2026-10-01" in title
    assert "VALID" in body
    assert all(symbol not in body for symbol in ("EARLY", "FUTURE", "CLOSED_CN"))
