"""Exercise HTTP triggers through the real server StockConfig construction."""

import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.modules.administration.api.auth import get_current_user
from src.modules.automation import agent_runs
from src.modules.automation.base import AnalysisResult
from src.modules.market.api import stocks
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import AgentConfig, AgentRun, Stock, StockAgent


@pytest.fixture
def trigger_api(tmp_path, monkeypatch):
    import server

    engine = create_engine(
        f"sqlite:///{tmp_path / 'trigger.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([
            AgentConfig(name=name, display_name=name)
            for name in ("daily_report", "tradingagents")
        ])
        db.commit()

    contexts, threads = [], []

    class RecordingAgent:
        async def run(self, context):
            contexts.append(context)
            return AnalysisResult(
                agent_name="test", title="Analysis", content="Analysis completed"
            )

    def tracked_thread(**kwargs):
        thread = threading.Thread(**kwargs)
        threads.append(thread)
        return thread

    def database():
        with sessions() as db:
            yield db

    # Keep routing, StockConfig, context construction and run persistence real;
    # replace only authentication and the external analysis/AI boundary.
    monkeypatch.setitem(server.app.dependency_overrides, get_db, database)
    monkeypatch.setitem(server.app.dependency_overrides, get_current_user, lambda: "test")
    monkeypatch.setattr(server, "SessionLocal", sessions)
    monkeypatch.setattr(agent_runs, "SessionLocal", sessions)
    monkeypatch.setattr(server, "_build_ai_client", lambda *args: SimpleNamespace())
    monkeypatch.setattr(stocks, "threading", SimpleNamespace(Thread=tracked_thread))
    for name in ("daily_report", "tradingagents"):
        monkeypatch.setitem(server.AGENT_REGISTRY, name, RecordingAgent)

    # No lifespan: background schedulers and provider warmups are unrelated.
    client = TestClient(server.app)
    try:
        yield client, sessions, contexts, threads
    finally:
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        client.close()
        engine.dispose()


@pytest.mark.parametrize("agent_name", ["daily_report", "tradingagents"])
@pytest.mark.parametrize("wait", [True, False], ids=["wait", "background"])
@pytest.mark.parametrize("symbol,name,market,expected_type", [
    ("AAPL", "", "US", "stock"),
    ("510300", "沪深300ETF", "CN", "etf"),
    ("QQQ", "Invesco QQQ ETF", "US", "etf"),
])
def test_unbound_trigger_builds_stock_config(
    trigger_api, agent_name, wait, symbol, name, market, expected_type
):
    client, sessions, contexts, threads = trigger_api
    params = {"allow_unbound": True, "symbol": symbol, "market": market}
    if name:
        params["name"] = name
    if wait:
        params["wait"] = True
    response = client.post(f"/api/stocks/0/agents/{agent_name}/trigger", params=params)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    data = payload["data"]
    if wait:
        assert data["result"]["content"] == "Analysis completed"
    else:
        assert data["queued"] is True
        assert len(threads) == 1
        threads[0].join(timeout=5)
        assert not threads[0].is_alive()

    assert len(contexts) == 1
    context = contexts[0]
    assert len(context.watchlist) == 1
    config = context.watchlist[0]
    assert (config.symbol, config.name, config.market.value, config.asset_type) == (
        symbol, name or symbol, market, expected_type
    )
    assert context.suppress_notify is True
    assert context.portfolio.all_positions == []
    assert context._trace_id == data["trace_id"]
    with sessions() as db:
        assert db.query(Stock).count() == 0
        assert db.query(StockAgent).count() == 0
        run = db.query(AgentRun).filter_by(trace_id=data["trace_id"]).one()
        assert run.status == "success"
        assert run.result == "Analysis completed"
        assert run.notify_sent is False


@pytest.mark.parametrize("by_symbol", [False, True], ids=["stock-id", "symbol-lookup"])
@pytest.mark.parametrize("symbol,name,market,asset_type", [
    ("AAPL", "Apple", "US", "stock"),
    ("02800", "盈富基金", "HK", "etf"),
    ("EXAMPLE", "ETF Services Company", "US", "stock"),
])
def test_existing_stock_trigger_preserves_explicit_type(
    trigger_api, by_symbol, symbol, name, market, asset_type
):
    client, sessions, contexts, _ = trigger_api
    with sessions() as db:
        stock = Stock(symbol=symbol, name=name, market=market, asset_type=asset_type)
        db.add(stock)
        db.flush()
        stock_id = stock.id
        db.add(StockAgent(stock_id=stock_id, agent_name="daily_report"))
        db.commit()

    params = {"wait": True}
    if by_symbol:
        params.update(allow_unbound=True, symbol=symbol, market=market, name="Other name")
    response = client.post(
        f"/api/stocks/{0 if by_symbol else stock_id}/agents/daily_report/trigger",
        params=params,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["result"]["content"] == "Analysis completed"
    assert len(contexts) == 1
    config = contexts[0].watchlist[0]
    assert (config.symbol, config.name, config.market.value, config.asset_type) == (
        symbol, name, market, asset_type
    )
    assert contexts[0].suppress_notify is by_symbol
    with sessions() as db:
        assert db.query(Stock).count() == 1
        assert db.get(Stock, stock_id).asset_type == asset_type


@pytest.mark.parametrize("params,error_code", [
    ({"symbol": "AAPL"}, "stock_agent_unbound_not_allowed"),
    ({"allow_unbound": True}, "stock_symbol_required"),
])
def test_unbound_trigger_still_requires_opt_in_and_symbol(trigger_api, params, error_code):
    client, sessions, contexts, threads = trigger_api
    response = client.post("/api/stocks/0/agents/daily_report/trigger", params=params)
    assert response.status_code == 400
    assert response.json()["error_code"] == error_code
    assert not contexts and not threads
    with sessions() as db:
        assert db.query(AgentRun).count() == 0
