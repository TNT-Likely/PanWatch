"""Homepage loading must avoid unused external calls and serial market waits."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.automation.api import agents
from src.modules.portfolio.api import accounts
from src.platform.persistence.database import Base
from src.platform.persistence.models import Account, Position, Stock
from src.platform.marketdata.models import MarketCode


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("markets", [[], ["CN"], ["HK"], ["US"], ["CN", "HK", "US"]])
def test_holdings_only_request_exchange_rates_for_held_markets(db, monkeypatch, markets):
    account = Account(name="Test", enabled=True)
    db.add(account)
    db.flush()
    for i, market in enumerate(markets):
        stock = Stock(symbol=f"STOCK-{i}", market=market, name=market)
        db.add(stock)
        db.flush()
        db.add(Position(account_id=account.id, stock_id=stock.id, cost_price=10, quantity=100))
    db.commit()
    hkd, usd = Mock(return_value=0.9), Mock(return_value=7.0)
    monkeypatch.setattr(accounts, "get_hkd_cny_rate", hkd)
    monkeypatch.setattr(accounts, "get_usd_cny_rate", usd)
    monkeypatch.setattr(accounts, "_fetch_quotes_for_stocks", lambda stocks: {
        stock.symbol: {"current_price": 12} for stock in stocks
    })

    result = accounts._gather_holdings(db)

    assert hkd.call_count == int("HK" in markets)
    assert usd.call_count == int("US" in markets)
    for item in result:
        fx = {"CN": 1, "HK": 0.9, "US": 7}[item["market"]]
        assert item["market_value"] == pytest.approx(1200 * fx)
        assert item["unrealized_pnl"] == pytest.approx(200 * fx)


def test_market_quotes_run_concurrently_and_preserve_partial_results(monkeypatch):
    started = {market: threading.Event() for market in ["CN", "HK", "US"]}
    release = threading.Event()

    def fetch(symbols, market):
        started[market].set()
        release.wait(3)
        if market == "HK":
            raise RuntimeError("unavailable")
        return [{"symbol": symbols[0], "current_price": 12}]

    monkeypatch.setattr(accounts, "md_quote_rows", fetch)
    stocks = [SimpleNamespace(symbol=market, market=market) for market in started]
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(accounts._fetch_quotes_for_stocks, stocks)
        try:
            assert all(event.wait(1) for event in started.values()), "market calls were serialized"
        finally:
            release.set()
        result = pending.result(timeout=3)
    assert set(result) == {"CN", "US"}


@pytest.fixture
def snapshot_setup(db, monkeypatch):
    import server
    from src.platform.marketdata import marketdata_client, models
    from src.platform.marketdata.collectors.kline_collector import KlineCollector

    with agents._SNAPSHOT_CACHE_LOCK:
        agents._SNAPSHOT_CACHE.clear()
    stock = SimpleNamespace(symbol="600519", name="贵州茅台", market=MarketCode.CN)
    quote = SimpleNamespace(
        symbol=stock.symbol, name=stock.name, change_pct=4.0, current_price=12,
        change_amount=0.5, open_price=11, high_price=12, low_price=11,
        prev_close=11.5, volume=100, turnover=1200,
    )
    portfolio = SimpleNamespace(
        get_positions_for_stock=lambda _: [SimpleNamespace(cost_price=10, trading_style="long")],
        total_available_funds=100,
    )
    monkeypatch.setattr(server, "load_watchlist_for_agent", lambda _: [stock])
    monkeypatch.setattr(server, "load_portfolio_for_agent", lambda _: portfolio)
    monkeypatch.setattr(models, "MARKETS", {MarketCode.CN: SimpleNamespace(is_trading_time=lambda: True)})
    monkeypatch.setattr(marketdata_client, "md_stock_data", lambda *_: [quote])
    kline = Mock(return_value={"trend": "bullish"})
    monkeypatch.setattr(KlineCollector, "get_kline_summary", kline)
    yield kline
    with agents._SNAPSHOT_CACHE_LOCK:
        agents._SNAPSHOT_CACHE.clear()


def test_homepage_snapshot_is_read_only_and_never_loads_klines(db, snapshot_setup, monkeypatch):
    import server
    ai_context = Mock(side_effect=AssertionError("snapshot must not invoke AI"))
    monkeypatch.setattr(server, "build_context", ai_context)
    result = asyncio.run(agents.intraday_snapshot(db=db))
    snapshot_setup.assert_not_called()
    ai_context.assert_not_called()
    row = result["stocks"][0]
    assert row["has_position"] is True
    assert row["pnl_pct"] == 20
    assert row["alert_type"] == "急涨"
    assert "suggestion" not in row
    assert asyncio.run(agents.intraday_snapshot(db=db)) == result


def test_manual_scan_routes_are_removed():
    paths = {route.path for route in agents.router.routes}
    assert "/intraday/scan" not in paths
    assert "/intraday/analysis" not in paths
    assert "/intraday/snapshot" in paths
