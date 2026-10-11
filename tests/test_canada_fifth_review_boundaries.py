"""Listing-scoped notification, announcement API and public history regressions.

The production entry points and SQLite persistence run unchanged. External
collection/model/notification transports use deterministic, non-sending fixtures.
"""
import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pan_agent import ModelMessage, RunRequest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.assistant import tools
from src.modules.automation import intraday_monitor
from src.modules.automation.base import AgentContext
from src.modules.research.api import insights
from src.platform.marketdata.models import MarketCode, StockData
from src.platform.persistence import database
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import AnalysisHistory, NotifyThrottle, Stock, StockContextSnapshot, StockSuggestion


@pytest.fixture
def sessions(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    insights._ANN_CACHE.clear()
    yield factory
    insights._ANN_CACHE.clear()
    engine.dispose()


def run_intraday(monkeypatch, market, *, success=True, bypass=False, symbol="T"):
    from src.modules.automation.tradingagents import operations

    quote = StockData(symbol=symbol, name=f"{market} listing", market=MarketCode(market),
        current_price=20, change_pct=4, change_amount=1, volume=100, turnover=2000,
        open_price=19, high_price=21, low_price=19, prev_close=19)
    agent = intraday_monitor.IntradayMonitorAgent(event_only=False, bypass_throttle=bypass)
    # Keep real analyze(), should_notify(), throttle queries and run_single().
    monkeypatch.setattr(agent, "collect", AsyncMock(return_value={"stock_data": quote}))
    for function in ("save_suggestion", "save_agent_context_run", "save_agent_prediction_outcome"):
        monkeypatch.setattr(intraday_monitor, function, lambda **kwargs: None)
    monkeypatch.setattr(operations, "try_auto_trigger", lambda *args, **kwargs: None)
    notifier = SimpleNamespace(notify_with_result=AsyncMock(return_value={"success": success}))
    context = AgentContext(ai_client=SimpleNamespace(chat=AsyncMock(return_value='{"action":"buy","should_alert":true,"reason":"fixture"}')),
        notifier=notifier, config=SimpleNamespace(watchlist=[SimpleNamespace(symbol=symbol, market=MarketCode(market))]))
    return asyncio.run(agent.run_single(context, symbol)), notifier


def test_intraday_notifications_are_scoped_across_exchanges_and_restart(sessions, monkeypatch):
    for market in ("US", "TSX", "TSXV"):
        result, notifier = run_intraday(monkeypatch, market)
        assert result.raw_data["notified"] is True, f"{market}:T incorrectly throttled by another listing"
        assert result.raw_data["stock"]["market"] == market
        assert notifier.notify_with_result.await_count == 1
    with sessions() as db:
        assert {r.stock_symbol for r in db.query(NotifyThrottle)} == {"US:T", "TSX:T", "TSXV:T"}
    for market in ("US", "TSX", "TSXV"):
        result, notifier = run_intraday(monkeypatch, market)
        assert result.raw_data["notified"] is False
        notifier.notify_with_result.assert_not_awaited()


@pytest.mark.parametrize("alias,canonical,market", [("REI.UN", "REI-UN", "TSX"), ("ENB.PR.V", "ENB-PV", "TSX"), ("RCK.V", "RCK", "TSXV")])
def test_throttle_normalizes_listing_aliases(sessions, alias, canonical, market):
    agent = intraday_monitor.IntradayMonitorAgent()
    assert agent._check_throttle(alias, market)
    agent._update_throttle(alias, market)
    assert not agent._check_throttle(canonical, market)
    with sessions() as db:
        assert db.query(NotifyThrottle).one().stock_symbol == f"{market}:{canonical}"


def test_legacy_ambiguous_throttle_is_preserved_but_not_assigned_to_a_listing(sessions, monkeypatch):
    with sessions() as db:
        db.add(NotifyThrottle(agent_name="intraday_monitor", stock_symbol="T", last_notify_at=datetime.now(UTC), notify_count=7))
        db.commit()
    for market in ("US", "TSX"):
        result, _ = run_intraday(monkeypatch, market)
        assert result.raw_data["notified"] is True
    with sessions() as db:
        legacy = db.query(NotifyThrottle).filter_by(stock_symbol="T").one()
        assert legacy.notify_count == 7
        assert db.query(NotifyThrottle).count() == 3


def test_throttle_failures_bypass_and_expiry_do_not_poison_state(sessions, monkeypatch):
    result, _ = run_intraday(monkeypatch, "TSX", success=False)
    assert result.raw_data["notified"] is False
    with sessions() as db:
        assert db.query(NotifyThrottle).count() == 0
    result, _ = run_intraday(monkeypatch, "TSX", bypass=True)
    assert result.raw_data["notified"] is True
    with sessions() as db:
        assert db.query(NotifyThrottle).count() == 0
        db.add(NotifyThrottle(agent_name="intraday_monitor", stock_symbol="TSX:T", last_notify_at=datetime.now(UTC)-timedelta(days=1), notify_count=9))
        db.commit()
    result, _ = run_intraday(monkeypatch, "TSX")
    assert result.raw_data["notified"] is True
    with sessions() as db:
        row = db.query(NotifyThrottle).one()
        assert row.stock_symbol == "TSX:T" and row.notify_count == 1


@pytest.mark.parametrize("alias,market,canonical", [
    (" rei.un ", "TSX", "REI-UN"), ("ENB.PR.V", "TSX", "ENB-PV"),
    ("RY.TO", "TSX", "RY"), ("RCK.V", "TSXV", "RCK"),
])
def test_public_research_tool_normalizes_all_history_sources(sessions, alias, market, canonical):
    with sessions() as db:
        db.add_all([
            StockSuggestion(stock_symbol=canonical, stock_market=market, agent_name="daily_report", action="watch", action_label="Watch", reason="right suggestion"),
            StockContextSnapshot(symbol=canonical, market=market, snapshot_date=date.today().isoformat(), context_type="daily_report", payload={"right": True}),
            AnalysisHistory(stock_symbol=f"{market}:{canonical}", agent_name="tradingagents", analysis_date=date.today().isoformat(), content="right report", raw_data={"market": market}),
            StockSuggestion(stock_symbol=canonical, stock_market="US", agent_name="daily_report", action="buy", action_label="Buy", reason="wrong issuer"),
        ])
        db.commit()
        registry = tools.build_panwatch_tool_registry(db)
        request = RunRequest(run_id="listing-history", messages=[ModelMessage(role="user", content="Read my research")])
        result = asyncio.run(registry.execute("get_research_history", request, {"symbol": alias, "market": market}))
    assert result.ok
    assert result.data["symbol"] == canonical
    assert result.data["count"] == 3
    assert {item["kind"] for item in result.data["items"]} == {"suggestion", "context_snapshot", "analysis_report"}
    assert "wrong issuer" not in str(result.data)


@pytest.mark.parametrize("symbol,market", [("RY.TO", "TSXV"), ("RCK.V", "TSX"), ("ENB.PR.V", "TSXV")])
def test_history_rejects_conflicting_exchange_aliases(sessions, symbol, market):
    with sessions() as db:
        request = RunRequest(run_id="invalid-history", messages=[ModelMessage(role="user", content="History")])
        result = asyncio.run(tools.build_panwatch_tool_registry(db).execute("get_research_history", request, {"symbol": symbol, "market": market}))
    assert not result.ok and result.error_code == "history_arguments_invalid"


def test_announcement_api_uses_canonical_listing_issuer_and_cache(sessions, monkeypatch):
    from src.platform.marketdata.collectors.news_collector import NewsCollector, NewsItem
    with sessions() as db:
        db.add_all([Stock(symbol="T", market="US", name="AT&T"), Stock(symbol="T", market="TSX", name="TELUS"), Stock(symbol="T", market="TSXV", name="Venture fixture")])
        db.commit()
    requests = []
    prompts = []
    async def fetch(self, symbols, since_hours, symbol_names, **kwargs):
        requests.append((symbols, symbol_names, kwargs.get("market")))
        return [NewsItem(source="fixture", external_id="announcement", title=next(iter(symbol_names.values())),
            content="Fixture release", publish_time=datetime.now(UTC), symbols=symbols)]
    async def chat(system, user, **kwargs):
        prompts.append((system, user))
        return "1|中性|Fixture"
    monkeypatch.setattr(NewsCollector, "fetch_all", fetch)
    monkeypatch.setattr(insights, "get_configured_failover_client", lambda *args: SimpleNamespace(chat=chat))
    app = FastAPI()
    app.include_router(insights.router)
    def get_session():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = get_session
    with TestClient(app) as client:
        for market, alias, name in [("US", "T", "AT&T"), ("TSX", " t.to ", "TELUS"), ("TSXV", "T.V", "Venture fixture")]:
            response = client.post("/announcement-eval", json={"symbol": alias, "market": market})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["symbol"] == "T" and body["market"] == market
            assert body["items"][0]["title"] == name
            assert requests[-1] == (["T"], {"T": name}, market)
            assert f"{name}({market}:T)" in prompts[-1][1]
            if market != "CN":
                assert "A股" not in prompts[-1][0]
            repeated = client.post("/announcement-eval", json={"symbol": "T", "market": market})
            assert repeated.json() == body
        assert len(requests) == len(prompts) == 3
        invalid = client.post("/announcement-eval", json={"symbol": "T.TO", "market": "TSXV"})
        assert invalid.status_code == 400
        assert len(requests) == 3


def test_announcement_collector_does_not_route_canada_to_cn_only_providers(monkeypatch):
    from marketdata import MarketData, SourceConfig, StaticConfigProvider
    from marketdata.vendors.news import EastmoneyStockNewsVendor
    from src.platform.marketdata import marketdata_client
    md = MarketData(StaticConfigProvider({"news": [SourceConfig(vendor="eastmoney_news")]}))
    monkeypatch.setattr(marketdata_client, "get_market_data", lambda: md)
    def wrong_exchange(*args, **kwargs):
        pytest.fail("Canadian announcement query reached CN-only news provider")
    monkeypatch.setattr(EastmoneyStockNewsVendor, "fetch", wrong_exchange)
    result = asyncio.run(insights._fetch_recent_announcements("T", "TELUS", market="TSX"))
    assert result == []


@pytest.mark.parametrize("market", ["US", "HK", "TSX", "TSXV"])
def test_public_news_tool_does_not_relabel_cn_news(sessions, monkeypatch, market):
    from marketdata import MarketData, SourceConfig, StaticConfigProvider
    from marketdata.vendors.news import EastmoneyStockNewsVendor
    from src.platform.marketdata import marketdata_client
    md = MarketData(StaticConfigProvider({"news": [SourceConfig(vendor="eastmoney_news")]}))
    monkeypatch.setattr(marketdata_client, "get_market_data", lambda: md)
    def wrong_exchange(*args, **kwargs):
        pytest.fail("Explicit foreign market reached CN-only news provider")
    monkeypatch.setattr(EastmoneyStockNewsVendor, "fetch", wrong_exchange)
    with sessions() as db:
        request = RunRequest(run_id="listing-news", messages=[ModelMessage(role="user", content="Read news")])
        result = asyncio.run(tools.build_panwatch_tool_registry(db).execute(
            "get_stock_news", request, {"symbol": "T" if market != "HK" else "00700", "market": market}))
    assert result.ok
    assert result.data["market"] == market
    assert result.data["items"] == []


def test_portfolio_api_matches_rendered_modal_fx_fixture(sessions, monkeypatch):
    import json
    from pathlib import Path
    from src.modules.portfolio.api import accounts as api
    from src.platform.marketdata import quote_display
    from src.platform.persistence.models import Account, Position
    with sessions() as db:
        account = Account(name="Canadian fixture", available_funds=0, enabled=True)
        stock = Stock(symbol="T", market="TSX", name="TELUS")
        db.add_all([account, stock])
        db.flush()
        db.add(Position(account_id=account.id, stock_id=stock.id, quantity=1.5, cost_price=10))
        db.commit()
    monkeypatch.setattr(quote_display.calendar, "market_status", lambda *args: "closed")
    monkeypatch.setattr(api, "_hkd_rate_cache", {"rate": .92, "ts": 0})
    monkeypatch.setattr(api, "_usd_rate_cache", {"rate": 7.25, "ts": 0})
    monkeypatch.setattr(api, "get_cad_cny_rate", lambda **kwargs: None)
    monkeypatch.setattr(api, "get_canada_usd_cny_rate", lambda **kwargs: None)
    monkeypatch.setattr(api, "get_usd_cny_rate", lambda: 7)
    monkeypatch.setattr(api, "get_hkd_cny_rate", lambda: .9)
    monkeypatch.setattr(api, "_fetch_quotes_for_stocks", lambda *args: {
        "TSX:T": {"current_price": 20, "currency": "CAD", "source_timestamp": "2026-10-09T20:00:00+00:00", "is_delayed": True},
    })
    app = FastAPI()
    app.include_router(api.router)
    def get_session():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = get_session
    with TestClient(app) as client:
        response = client.get("/portfolio/summary", params={"include_quotes": "true"})
    assert response.status_code == 200
    fixture = Path(__file__).resolve().parents[1] / "frontend/tests/fixtures/stock-insight-fx-unavailable.json"
    assert response.json() == json.loads(fixture.read_text())


@pytest.fixture
def news_boundary(sessions, monkeypatch):
    from marketdata import MarketData, SourceConfig, StaticConfigProvider
    from marketdata.types import NewsArticle
    from src.modules.market.api import news as news_api
    from src.platform.marketdata import marketdata_client
    requests = []
    class FixtureNews:
        # Synthetic multi-market provider tests that scoping is preserved when
        # a compatible vendor exists; this is not live Canadian news coverage.
        supports_markets = {"CN", "HK", "US", "TSX", "TSXV"}
        def fetch(self, symbols, config):
            rows = []
            for symbol in symbols:
                name = config["symbol_names"].get(symbol.code) or symbol.code
                requests.append((symbol.market.value, symbol.code, name))
                rows.append(NewsArticle(source="fixture", external_id=f"{symbol.market.value}:{symbol.code}",
                    title=name + " release", content="fixture", publish_time=datetime.now(UTC), symbols=[symbol.code]))
            return rows
    md = MarketData(StaticConfigProvider({"news": [SourceConfig(vendor="eastmoney_news")]}))
    md._news_vendors["eastmoney_news"] = FixtureNews()
    monkeypatch.setattr(marketdata_client, "get_market_data", lambda: md)
    app = FastAPI()
    app.include_router(news_api.router, prefix="/news")
    def get_session():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = get_session
    with sessions() as db:
        db.add_all([Stock(symbol="T", market="TSX", name="TELUS"), Stock(symbol="T", market="US", name="AT&T"),
            Stock(symbol="T", market="TSXV", name="Venture"), Stock(symbol="REI-UN", market="TSX", name="RioCan"),
            Stock(symbol="600519", market="CN", name="Moutai")])
        db.commit()
    with TestClient(app) as client:
        yield client, md, requests


@pytest.mark.parametrize("params,expected", [
    ({"names": "TELUS", "market": "TSX"}, [("TSX", "T", "TELUS")]),
    ({"symbols": "T", "market": "TSX"}, [("TSX", "T", "TELUS")]),
    ({"symbols": "T", "market": "US"}, [("US", "T", "AT&T")]),
    ({"symbols": "T", "market": "TSXV"}, [("TSXV", "T", "Venture")]),
    ({"symbols": "REI.UN", "market": "TSX"}, [("TSX", "REI-UN", "RioCan")]),
    ({"market": "TSX"}, [("TSX", "T", "TELUS"), ("TSX", "REI-UN", "RioCan")]),
    ({"names": "AT&T", "market": "TSX"}, []),
    ({"symbols": "600519"}, [("CN", "600519", "Moutai")]),
])
def test_news_api_scopes_every_modal_query_and_keeps_cn_legacy(news_boundary, params, expected):
    client, _, requests = news_boundary
    response = client.get("/news", params=params)
    assert response.status_code == 200, response.text
    assert requests == expected
    assert {item["title"] for item in response.json()} == {name + " release" for _, _, name in expected}


@pytest.mark.parametrize("params", [
    {"names": "TELUS", "market": "TSX"}, {"symbols": "T", "market": "TSX"},
    {"market": "TSX"}, {"symbols": "T", "market": "TSXV"},
])
def test_news_api_unsupported_provider_stays_empty_through_modal_fallbacks(news_boundary, monkeypatch, params):
    from marketdata.vendors.news import EastmoneyStockNewsVendor
    client, md, requests = news_boundary
    md._news_vendors["eastmoney_news"] = EastmoneyStockNewsVendor()
    def wrong_exchange(*args, **kwargs):
        pytest.fail("Scoped Canadian news fallback reached CN-only provider")
    monkeypatch.setattr(EastmoneyStockNewsVendor, "fetch", wrong_exchange)
    response = client.get("/news", params=params)
    assert response.status_code == 200 and response.json() == []
    assert requests == []


@pytest.mark.parametrize("params", [{"symbols": "T.TO", "market": "TSXV"}, {"symbols": "T", "market": "invalid"}])
def test_news_api_rejects_conflicting_scope_before_request(news_boundary, params):
    client, _, requests = news_boundary
    assert client.get("/news", params=params).status_code == 400
    assert requests == []


def test_signalpack_news_requests_cache_and_payload_are_listing_scoped(news_boundary, monkeypatch):
    from src.modules.automation.base import PortfolioInfo
    from src.modules.research.signals.signal_pack import SignalPackBuilder
    _, _, requests = news_boundary
    monkeypatch.setattr(SignalPackBuilder, "_source_policy", staticmethod(lambda *a, **kw: ([], True)))
    builder = SignalPackBuilder()
    for market, name in [("US", "AT&T"), ("TSX", "TELUS"), ("TSXV", "Venture")]:
        for _ in range(2):
            packs = asyncio.run(builder.build_for_symbols(symbols=[("T", MarketCode(market), name)],
                include_news=True, news_hours=168, portfolio=PortfolioInfo(),
                include_technical=False, include_capital_flow=False, include_events=False))
            assert [item["title"] for item in packs["T"].news.items] == [name + " release"]
            assert "news" not in packs["T"].missing
    assert requests == [("US", "T", "AT&T"), ("TSX", "T", "TELUS"), ("TSXV", "T", "Venture")]


def test_signalpack_marks_canadian_news_unavailable_for_cn_only_sources(news_boundary, monkeypatch):
    from marketdata.vendors.news import EastmoneyStockNewsVendor
    from src.modules.automation.base import PortfolioInfo
    from src.modules.research.signals.signal_pack import SignalPackBuilder
    _, md, _ = news_boundary
    md._news_vendors["eastmoney_news"] = EastmoneyStockNewsVendor()
    monkeypatch.setattr(SignalPackBuilder, "_source_policy", staticmethod(lambda *a, **kw: ([], True)))
    def wrong_exchange(*args, **kwargs):
        pytest.fail("Canadian scheduled news reached CN-only provider")
    monkeypatch.setattr(EastmoneyStockNewsVendor, "fetch", wrong_exchange)
    packs = asyncio.run(SignalPackBuilder().build_for_symbols(symbols=[("T", MarketCode.TSX, "TELUS")],
        include_news=True, news_hours=168, portfolio=PortfolioInfo(),
        include_technical=False, include_capital_flow=False, include_events=False))
    assert packs["T"].news.items == []
    assert "news" in packs["T"].missing
    assert packs["T"].sources["news"] == "unavailable"
