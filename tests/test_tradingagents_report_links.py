"""Offline execution → persisted report → notification/detail API contract."""

import asyncio
import re
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.automation.api import agents as agents_api
from src.modules.automation.tradingagents import agent as agent_module
from src.modules.automation.tradingagents import decision
from src.modules.research import analysis_history, analysis_link
from src.platform.marketdata.instrument_identity import analysis_key, analysis_trade_date
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import AnalysisHistory, AppSettings


@pytest.fixture
def report_store(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(analysis_history, "SessionLocal", sessions)
    monkeypatch.setattr(analysis_link, "SessionLocal", sessions)
    with sessions() as db:
        db.add(AppSettings(key="panwatch_base_url", value="https://example.test/"))
        db.commit()
    yield sessions
    engine.dispose()


@pytest.mark.parametrize("start", [
    datetime(2026, 10, 10, 2, tzinfo=timezone.utc),
    datetime(2026, 1, 6, 4, tzinfo=timezone.utc),
])
@pytest.mark.parametrize("market,symbol,security_type,provider_symbol", [
    ("TSX", "T", "EQUITY", "T.TO"),
    ("TSX", "ZSP-U", "ETF", "ZSP-U.TO"),
    ("TSX", "REI-UN", "REIT", "REI-UN.TO"),
    # Synthetic namesake fixtures exercise all types on both exchanges.
    ("TSXV", "T", "EQUITY", "T.V"),
    ("TSXV", "ZSP-U", "ETF", "ZSP-U.V"),
    ("TSXV", "REI-UN", "REIT", "REI-UN.V"),
    ("US", "T", "EQUITY", "T"),
    ("CN", "600519", "EQUITY", "600519"),
    ("HK", "00700", "EQUITY", "00700"),
])
def test_execution_links_to_exact_saved_report_across_midnight(
    monkeypatch, report_store, market, symbol, security_type, provider_symbol, start,
):
    from tradingagents.graph import trading_graph
    from src.modules.automation import suggestion_pool

    expected_key = analysis_key(symbol, market)
    expected_date = analysis_trade_date(market, start)
    clock = SimpleNamespace(now=start)
    date_calls = []
    graph_calls = []

    def execution_date(market):
        date_calls.append(clock.now)
        return analysis_trade_date(market, clock.now)

    def unexpected_recomputation(*args, **kwargs):
        pytest.fail("The mapper must use the execution's exact persistence identity")

    class FixtureGraph:
        def __init__(self, **kwargs):
            self.propagator = None

        def _get_provider_kwargs(self):
            return {}

        def propagate(self, ticker, trade_date, portfolio=None):
            graph_calls.append((ticker, trade_date))
            # A long graph run crosses Toronto midnight in both DST regimes.
            clock.now += timedelta(hours=3)
            return {"final_trade_decision": "Rating: Hold\nFixture report."}, "HOLD"

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", FixtureGraph)
    monkeypatch.setattr(agent_module, "apply_compat_patches", lambda: None)
    monkeypatch.setattr(agent_module, "inject_api_key_env", lambda *a: None)
    monkeypatch.setattr(agent_module, "patch_route_to_vendor", nullcontext)
    monkeypatch.setattr(agent_module, "panwatch_data_context", lambda *a, **k: nullcontext())
    monkeypatch.setattr(agent_module, "build_stock_metadata_context", lambda **k: "")
    monkeypatch.setattr(agent_module, "build_ta_llm_config", lambda *a, **k: {
        "selected_analysts": ["market"], "deep_think_llm": "fixture", "quick_think_llm": "fixture",
    })
    monkeypatch.setattr(agent_module, "analysis_trade_date", execution_date)
    monkeypatch.setattr(decision, "analysis_trade_date", unexpected_recomputation)
    monkeypatch.setattr(decision, "make_analysis_key", unexpected_recomputation)
    monkeypatch.setattr(suggestion_pool, "save_suggestion", lambda **k: None)
    agent = agent_module.TradingAgentsAgent(cache_ttl_hours=0)
    agent._available = True
    monkeypatch.setattr(agent, "_collect_toolkit_diagnostic", lambda *a: {})
    stock = SimpleNamespace(symbol=symbol, market=MarketCode(market), name=f"{market} fixture")
    context = SimpleNamespace(
        _force_refresh=True, _trace_id="report-link-fixture", report_language="en-US",
        model_label="fixture", ai_client=SimpleNamespace(), portfolio=None,
    )

    # Decoy rows make a wrong ticker or next-day link return the wrong report,
    # rather than merely making this test pass because the API returned null.
    with report_store() as db:
        for decoy_key in {symbol, f"TSX:{symbol}", f"TSXV:{symbol}"} - {expected_key}:
            db.add(AnalysisHistory(
                agent_name="tradingagents", stock_symbol=decoy_key, analysis_date=expected_date,
                title="Wrong exchange", content="Keep historical exchange report",
            ))
        next_date = (datetime.fromisoformat(expected_date) + timedelta(days=1)).date().isoformat()
        db.add(AnalysisHistory(
            agent_name="tradingagents", stock_symbol=expected_key, analysis_date=next_date,
            title="Wrong date", content="Keep historical date report",
        ))
        db.commit()

    result = asyncio.run(agent.analyze(context, {
        "stock": stock, "quote": {"current_price": 42, "currency": "CAD", "security_type": security_type},
    }))
    assert date_calls == [start]
    assert graph_calls == [(provider_symbol, expected_date)]
    expected_url = f"https://example.test/analysis/{quote(expected_key, safe='')}/{expected_date}"
    for content in (result.content, result.notify_content):
        assert re.findall(r"\]\((https://example\.test/[^)]+)\)", content) == [expected_url]
    if market in {"TSX", "TSXV"}:
        assert expected_date != start.date().isoformat()
        assert expected_date != analysis_trade_date(market, clock.now)

    # Mirror the detail page's decoded route parameters through the real API
    # at the root deployment path. The graph is a fixture; this is backend
    # integration coverage, not browser, live-model, or notification delivery QA.
    link_key, link_date = [unquote(part) for part in urlsplit(expected_url).path.split("/")[-2:]]
    app = FastAPI()
    app.include_router(agents_api.router, prefix="/api/agents")

    def db_dependency():
        with report_store() as db:
            yield db

    app.dependency_overrides[get_db] = db_dependency
    with TestClient(app) as client:
        response = client.get("/api/agents/tradingagents/analysis", params={
            "stock_symbol": link_key, "analysis_date": link_date,
        })
    assert response.status_code == 200
    saved = response.json()
    assert saved["stock_symbol"] == expected_key
    assert saved["analysis_date"] == expected_date
    assert saved["title"] == result.title
    assert saved["content"] == result.content
    assert saved["raw_data"]["market"] == market
    assert saved["raw_data"]["security_type"] == security_type
    with report_store() as db:
        decoys = db.query(AnalysisHistory).filter(AnalysisHistory.id != saved["id"]).all()
        assert {row.content for row in decoys} <= {
            "Keep historical exchange report", "Keep historical date report",
        }
        assert len(decoys) == 3


@pytest.mark.parametrize("market,symbol", [("TSX", "T"), ("TSXV", "REI-UN"), ("US", "T"), ("CN", "600519"), ("HK", "00700")])
def test_direct_mapper_remains_market_aware_without_new_arguments(monkeypatch, report_store, market, symbol):
    start = datetime(2026, 10, 10, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(decision, "analysis_trade_date", lambda market: analysis_trade_date(market, start))
    result = decision.map_state_to_result(
        stock=SimpleNamespace(symbol=symbol, market=MarketCode(market), name="Fixture"),
        ta_result={"decision": "hold", "final_state": {}},
    )
    expected_key = quote(analysis_key(symbol, market), safe="")
    assert f"/analysis/{expected_key}/{analysis_trade_date(market, start)}" in result.notify_content


def test_mapper_uses_supplied_identity_verbatim(monkeypatch, report_store):
    result = decision.map_state_to_result(
        stock=SimpleNamespace(symbol="T", market=MarketCode.US, name="Display metadata"),
        ta_result={"decision": "hold", "final_state": {}},
        analysis_key="TSX:T", analysis_date="2026-01-05",
    )
    assert "/analysis/TSX%3AT/2026-01-05" in result.notify_content


def test_link_segments_are_encoded_with_a_trailing_base_url_slash():
    assert analysis_link.analysis_detail_url(
        "TSX:BRK/A ?#%()", "2026-10-09", "https://example.test/",
    ) == "https://example.test/analysis/TSX%3ABRK%2FA%20%3F%23%25%28%29/2026-10-09"
