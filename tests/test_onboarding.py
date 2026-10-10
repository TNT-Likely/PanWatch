"""Verified setup facts, refresh recovery, and bounded first-analysis execution."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.administration.api import channels, onboarding, providers, settings
from src.modules.administration.onboarding import (
    STATE_KEY, channel_fingerprint, model_fingerprint, read_value, record_check,
    recover_interrupted_analysis, write_value,
)
from src.modules.automation.first_analysis import FirstAnalysisAgent
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import AIModel, AIService, AgentRun, AppSettings, NotifyChannel, PriceAlertRule, Stock


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    for router, prefix in [(onboarding.router, "onboarding"), (providers.router, "providers"),
                           (channels.router, "channels"), (settings.router, "settings")]:
        app.include_router(router, prefix=f"/api/{prefix}")
    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    with factory() as db:
        db.add_all([Stock(symbol="600519", name="示例", market="CN"), Stock(symbol="AAPL", name="Apple", market="US")])
        db.commit()
    return TestClient(app), factory


def seed_model(factory):
    with factory() as db:
        service = AIService(name="QA", base_url="http://127.0.0.1/v1", api_key="synthetic-key")
        db.add(service); db.flush()
        model = AIModel(name="QA", model="qa-model", service_id=service.id, is_default=True)
        db.add(model); db.commit()
        return model.id, service.id


def select_with_quote(client, monkeypatch, goal="ai"):
    monkeypatch.setattr("src.platform.marketdata.marketdata_client.md_quote_rows", lambda *a: [
        {"symbol": "600519", "current_price": 123.4, "change_pct": 1.2,
         "source": "controlled", "source_timestamp": "2026-10-09T15:00:00+08:00"}])
    assert client.patch("/api/onboarding", json={"stock_id": 1, "goal": goal}).status_code == 200
    return client.post("/api/onboarding/quote")


def test_examples_and_old_browser_flags_cannot_complete_setup(setup):
    client, factory = setup
    status = client.get("/api/onboarding").json()
    assert status["completed_count"] == 0 and not status["started"]
    assert status["selected_stock"] is None and not status["completed"]
    with factory() as db:
        assert db.query(AppSettings).count() == 0
        assert db.query(AgentRun).count() == 0
    assert not client.patch("/api/onboarding", json={"completed": True}).json().get("completed")
    assert client.put("/api/settings/onboarding.progress", json={"value": "{}"}).status_code == 400


def test_defer_and_optional_skip_persist_without_completing(setup):
    client, factory = setup
    result = client.patch("/api/onboarding", json={"deferred": True, "skip": "notify"}).json()
    assert result["deferred"] and not result["completed"]
    result = client.get("/api/onboarding").json()
    assert next(step for step in result["steps"] if step["key"] == "notify")["status"] == "skipped"
    result = client.patch("/api/onboarding", json={"deferred": False, "unskip": "notify"}).json()
    assert not result["deferred"] and result["completed_count"] == 0


def test_quotes_goal_requires_explicit_selection_and_real_quote(setup, monkeypatch):
    client, factory = setup
    assert client.post("/api/onboarding/quote").status_code == 400
    result = select_with_quote(client, monkeypatch, "quotes").json()
    assert result["completed"] and result["completed_count"] == 2
    assert result["quote"]["current_price"] == 123.4
    assert result["quote"]["source_time"] == "2026-10-09T15:00:00+08:00"
    assert client.get("/api/onboarding").json()["completed"]
    result = client.patch("/api/onboarding", json={"stock_id": 2}).json()
    assert result["quote"] is None and result["completed_count"] == 1


@pytest.mark.parametrize("rows", [[], [{"symbol": "WRONG", "current_price": 100}],
                                   [{"symbol": "600519", "current_price": float("nan")}],
                                   [{"symbol": "600519", "current_price": 0}]])
def test_bad_quotes_do_not_advance_progress(setup, monkeypatch, rows):
    client, factory = setup
    client.patch("/api/onboarding", json={"stock_id": 1, "goal": "quotes"})
    monkeypatch.setattr("src.platform.marketdata.marketdata_client.md_quote_rows", lambda *a: rows)
    assert client.post("/api/onboarding/quote").status_code == 503
    assert client.get("/api/onboarding").json()["completed_count"] == 1


def test_quote_result_for_old_selection_is_discarded(setup, monkeypatch):
    client, factory = setup
    client.patch("/api/onboarding", json={"stock_id": 1})
    def collect(*args):
        with factory() as db:
            write_value(db, STATE_KEY, {"stock_id": 2}); db.commit()
        return [{"symbol": "600519", "current_price": 100}]
    monkeypatch.setattr("src.platform.marketdata.marketdata_client.md_quote_rows", collect)
    assert client.post("/api/onboarding/quote").status_code == 409
    assert client.get("/api/onboarding").json()["quote"] is None


def test_model_test_is_saved_and_invalidated_by_config_changes_or_failure(setup, monkeypatch):
    client, factory = setup
    model_id, service_id = seed_model(factory)
    async def reply(*a, **kw): return "OK"
    monkeypatch.setattr(providers.AIClient, "chat", reply)
    assert not client.get("/api/onboarding").json()["models"][0]["verified"]
    assert client.post(f"/api/providers/models/{model_id}/test").status_code == 200
    status = client.get("/api/onboarding").json()
    assert status["models"][0]["verified"]
    assert "synthetic-key" not in str(status)
    assert client.put(f"/api/providers/services/{service_id}", json={"api_key": "changed-key"}).status_code == 200
    assert not client.get("/api/onboarding").json()["models"][0]["verified"]
    client.post(f"/api/providers/models/{model_id}/test")
    async def failure(*a, **kw): raise RuntimeError("authentication failed")
    monkeypatch.setattr(providers.AIClient, "chat", failure)
    assert client.post(f"/api/providers/models/{model_id}/test").status_code == 400
    assert not client.get("/api/onboarding").json()["models"][0]["verified"]


def test_saved_channel_is_not_verified_until_successful_test(setup, monkeypatch):
    client, factory = setup
    response = client.post("/api/channels", json={"name": "QA", "type": "webhook", "config": {"url": "http://127.0.0.1/qa"}})
    channel_id = response.json()["id"]
    monkeypatch.setattr(channels.NotifierManager, "add_channel", lambda *a: None)
    async def success(*a, **kw): return {"success": True}
    monkeypatch.setattr(channels.NotifierManager, "notify_with_result", success)
    assert not client.get("/api/onboarding").json()["channels"][0]["verified"]
    assert client.post(f"/api/channels/{channel_id}/test").status_code == 200
    assert client.get("/api/onboarding").json()["channels"][0]["verified"]
    client.put(f"/api/channels/{channel_id}", json={"config": {"url": "http://127.0.0.1/changed"}})
    assert not client.get("/api/onboarding").json()["channels"][0]["verified"]
    client.post(f"/api/channels/{channel_id}/test")
    async def failure(*a, **kw): raise RuntimeError("receiver unavailable")
    monkeypatch.setattr(channels.NotifierManager, "notify_with_result", failure)
    assert client.post(f"/api/channels/{channel_id}/test").status_code == 500
    assert not client.get("/api/onboarding").json()["channels"][0]["verified"]
    client.put(f"/api/channels/{channel_id}", json={"enabled": False})
    assert not client.get("/api/onboarding").json()["channels"][0]["verified"]


def test_only_enabled_unexpired_alerts_for_selected_stock_count(setup):
    client, factory = setup
    client.patch("/api/onboarding", json={"stock_id": 1})
    with factory() as db:
        db.add_all([PriceAlertRule(stock_id=2, enabled=True), PriceAlertRule(stock_id=1, enabled=False),
                    PriceAlertRule(stock_id=1, enabled=True, expire_at=datetime.utcnow() - timedelta(days=1))]); db.commit()
    def alert_status(): return next(step for step in client.get("/api/onboarding").json()["steps"] if step["key"] == "alert")["status"]
    assert alert_status() == "pending"
    with factory() as db:
        db.add(PriceAlertRule(stock_id=1, enabled=True)); db.commit()
    assert alert_status() == "complete"


def test_first_analysis_rejects_unverified_model_and_deduplicates_running_task(setup, monkeypatch):
    client, factory = setup
    select_with_quote(client, monkeypatch)
    model_id, service_id = seed_model(factory)
    assert client.post("/api/onboarding/analysis").status_code == 400
    with factory() as db:
        record_check(db, "model", model_id, model_fingerprint(db.get(AIModel, model_id), db.get(AIService, service_id)), True)
    class Worker:
        def __init__(self, **kwargs): pass
        def start(self): pass
    monkeypatch.setattr(onboarding.threading, "Thread", Worker)
    first = client.post("/api/onboarding/analysis").json()
    second = client.post("/api/onboarding/analysis").json()
    assert first["analysis"]["id"] == second["analysis"]["id"]
    assert first["analysis"]["status"] == "running" and not first["completed"]
    assert client.patch("/api/onboarding", json={"stock_id": 2}).status_code == 409
    with factory() as db:
        assert db.query(AgentRun).count() == 1
        recover_interrupted_analysis(db)
    assert client.get("/api/onboarding").json()["analysis"]["error_code"] == "onboarding_analysis_interrupted"
    assert client.post("/api/onboarding/analysis").json()["analysis"]["id"] != first["analysis"]["id"]


def test_successful_analysis_is_required_for_ai_completion(setup, monkeypatch):
    client, factory = setup
    select_with_quote(client, monkeypatch)
    model_id, service_id = seed_model(factory)
    with factory() as db:
        record_check(db, "model", model_id, model_fingerprint(db.get(AIModel, model_id), db.get(AIService, service_id)), True)
        run = AgentRun(agent_name="first_analysis", trace_id="qa-success", status="success", result="Observed quote with evidence")
        db.add(run)
        state = read_value(db, STATE_KEY); state["analysis_trace_id"] = run.trace_id; write_value(db, STATE_KEY, state); db.commit()
    assert client.get("/api/onboarding").json()["completed"]
    client.patch("/api/onboarding", json={"stock_id": 2})
    assert not client.get("/api/onboarding").json()["completed"]


def test_first_analysis_rejects_empty_model_output():
    async def empty(*a, **kw): return " "
    context = SimpleNamespace(ai_client=SimpleNamespace(chat=empty), report_language="en-US")
    with pytest.raises(ValueError, match="onboarding_analysis_empty"):
        asyncio.run(FirstAnalysisAgent().analyze(context, {"symbol": "AAPL"}))


@pytest.mark.parametrize("constructor_fails", [False, True])
def test_background_analysis_settles_durably_through_agent_runtime(setup, monkeypatch, constructor_fails):
    client, factory = setup
    with factory() as db:
        run = AgentRun(agent_name="first_analysis", trace_id="background-qa", status="running")
        db.add(run); db.commit()
        run_id, bind = run.id, db.get_bind()
    calls = []
    class ModelClient:
        def __init__(self, **kw):
            if constructor_fails:
                raise RuntimeError("invalid model configuration")
            self.client = self
        async def chat(self, *a, **kw):
            calls.append("model")
            return "## Observation\nEvidence from the quote; historical data unavailable."
        async def close(self): calls.append("closed")
    async def unexpected_notification(*a, **kw):
        pytest.fail("first analysis must suppress notifications")
    monkeypatch.setattr("src.platform.ai.ai_client.AIClient", ModelClient)
    monkeypatch.setattr("src.platform.notifications.notifier.NotifierManager.notify_with_result", unexpected_notification)
    monkeypatch.setattr("src.platform.marketdata.marketdata_client.md_quote_rows", lambda *a: [
        {"symbol": "600519", "current_price": 123.4, "source": "controlled"}])
    def missing_history(*a): raise RuntimeError("history unavailable")
    monkeypatch.setattr("src.platform.marketdata.collectors.kline_collector.KlineCollector.get_technical_indicators", missing_history)
    snapshot = {"symbol": "600519", "market": "CN", "name": "QA", "base_url": "http://127.0.0.1/v1",
                "api_key": "synthetic", "model": "qa", "model_label": "QA", "language": "en-US", "trace_id": "background-qa"}
    asyncio.run(onboarding._execute_analysis(bind, run_id, snapshot))
    with factory() as db:
        saved = db.get(AgentRun, run_id)
        assert saved.status == ("failed" if constructor_fails else "success")
        if constructor_fails:
            assert saved.error and not calls
        else:
            assert "Evidence from the quote" in saved.result
            assert calls == ["model", "closed"]
