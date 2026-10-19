"""Host integration over real graphs and isolated transactions; models/data are controlled."""
import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.modules.automation.base import AgentContext, PortfolioInfo
from src.modules.automation.daily_report import DailyReportAgent
from src.modules.automation.premarket_outlook import PremarketOutlookAgent
from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.modules.research.orchestration import entry, repository, service
from src.modules.research.orchestration.models import ResearchRun, ResearchArtifact, ResearchDelivery, ResearchOutbox
from src.platform.persistence.database import Base
from src.platform.persistence.models import AgentConfig, StockSuggestion, AgentPredictionOutcome, AnalysisHistory
from src.platform.marketdata.models import MarketCode, StockData
from src.platform.runtime.config import AppConfig, Settings, StockConfig
from src.platform.notifications.notify_policy import NotifyPolicy

spec = importlib.util.spec_from_file_location("investment_test_models", Path(__file__).parents[1] / "packages/pan-investment-research/tests/test_research.py")
scripts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scripts)


@pytest.fixture
def host(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'research.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(repository, "engine", engine)
    monkeypatch.setattr(entry, "engine", engine)
    model = scripts.Model()
    monkeypatch.setattr(service, "ResearchModelGateway", lambda client: model)
    yield engine, model
    engine.dispose()


def make_context(trace="test-host", keys=((MarketCode.CN, "600519"),)):
    notifier = SimpleNamespace(notify_with_result=AsyncMock(return_value={"success": True}))
    context = AgentContext(SimpleNamespace(model="script"), notifier,
        AppConfig(Settings(_env_file=None), [StockConfig(symbol=s, market=m, name=f"QA {s}") for m, s in keys]),
        portfolio=PortfolioInfo(), suppress_notify=True)
    context._trace_id = trace
    return context


def configure(engine, name, mode="langgraph", **policy):
    with Session(engine) as db, db.begin():
        db.add(AgentConfig(name=name, display_name=name, enabled=False, config={"research_graph": {
            "engine": mode, "specialists": ["technical", "flow", "events"], "max_attempts": 25,
            "max_total_tokens": 250000, **policy}}))


async def collected(context):
    packs = {}
    for stock in context.watchlist:
        quote = StockData(stock.symbol, stock.name, stock.market, 100, 0, 0, 100, 10000, 100, 101, 99, 100)
        packs[stock.symbol] = SimpleNamespace(quote=quote, technical={"volume_ratio": 1}, missing=[],
            news=SimpleNamespace(items=[]), events=SimpleNamespace(items=[]), capital_flow={"net": 10}, sources={"quote": "fixture"})
    first = next(iter(packs.values()))
    return {"signal_packs": packs, "signal_pack": first, "stock_data": first.quote,
            "symbol_contexts": {}, "timestamp": datetime.now(timezone.utc).isoformat()}


@pytest.mark.parametrize("agent_cls", [DailyReportAgent, PremarketOutlookAgent, IntradayMonitorAgent])
def test_all_three_entrypoints_produce_persisted_graph_results(host, agent_cls, monkeypatch):
    engine, model = host
    agent = agent_cls()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, "collect", collected)
    context = make_context()
    result = asyncio.run(agent.run(context))
    assert result.raw_data["engine"] == "langgraph"
    assert result.raw_data["result_status"] == "ready", result.content
    context.notifier.notify_with_result.assert_not_awaited()
    with Session(engine) as db:
        assert len(db.scalars(select(StockSuggestion)).all()) == 1
        assert len(db.scalars(select(AgentPredictionOutcome)).all()) == 2
        assert len(db.scalars(select(ResearchDelivery)).all()) == 1
        assert len(db.scalars(select(AnalysisHistory)).all()) == 1
    assert model.calls["strategy"] == model.calls["risk"] == 1


def test_shadow_records_artifacts_but_leaves_business_delivery_to_legacy(host, monkeypatch):
    engine, _ = host
    agent = DailyReportAgent()
    configure(engine, agent.name, "shadow")
    monkeypatch.setattr(agent, "collect", collected)
    context = make_context()
    assert asyncio.run(entry.maybe_run_research(agent, context)) is None
    with Session(engine) as db:
        assert len(db.scalars(select(ResearchArtifact)).all()) == 9
        assert not db.scalars(select(StockSuggestion)).all()
        assert not db.scalars(select(ResearchOutbox)).all()
        assert not db.scalars(select(AnalysisHistory)).all()


def test_business_transaction_rolls_back_every_write_on_required_failure(host, monkeypatch):
    engine, _ = host
    agent = DailyReportAgent()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, "collect", collected)
    monkeypatch.setattr(repository, "save_agent_prediction_outcome", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("controlled write failure")))
    context = make_context()
    with pytest.raises(RuntimeError, match="controlled"):
        asyncio.run(agent.run(context))
    with Session(engine) as db:
        assert not db.scalars(select(StockSuggestion)).all()
        assert not db.scalars(select(ResearchDelivery)).all()
        assert not db.scalars(select(ResearchOutbox)).all()
        assert db.scalars(select(ResearchRun)).one().status == "failed"
    context.notifier.notify_with_result.assert_not_awaited()


def test_same_request_does_not_repeat_models_predictions_or_delivery(host, monkeypatch):
    engine, model = host
    agent = DailyReportAgent()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, "collect", collected)
    context = make_context()
    first = asyncio.run(agent.run(context))
    calls = sum(model.calls.values())
    second = asyncio.run(agent.run(context))
    assert second.content == first.content
    assert sum(model.calls.values()) == calls
    with Session(engine) as db:
        assert len(db.scalars(select(AgentPredictionOutcome)).all()) == 2


def test_intraday_single_scope_does_not_mutate_watchlist_or_trigger_legacy_ta(host, monkeypatch):
    engine, _ = host
    agent = IntradayMonitorAgent(event_only=False)
    configure(engine, agent.name)
    monkeypatch.setattr(agent, "collect", collected)
    context = make_context(keys=((MarketCode.CN, "600519"), (MarketCode.US, "AAPL")))
    original = list(context.watchlist)
    result = asyncio.run(agent.run_single(context, "600519"))
    assert context.watchlist == original
    assert result.raw_data["scope"] == ["CN:600519"]
    assert result.raw_data["usage"]["attempts"] >= 5  # no-event still executes research


def test_missing_previous_reports_does_not_block_premarket(host, monkeypatch):
    engine, _ = host
    agent = PremarketOutlookAgent()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, "collect", collected)
    result = asyncio.run(agent.run(make_context()))
    assert result.raw_data["result_status"] == "ready"


def test_policy_rejects_unbounded_or_unknown_settings():
    for invalid in ({"engine": "unknown"}, {"deadline_seconds": 0}, {"max_attempts": 10000}, {"arbitrary": True}):
        with pytest.raises(ValueError):
            service.validate_config(invalid, "daily_report")


def test_migration_can_create_research_schema_twice(tmp_path):
    from src.platform.persistence.migrations import _m138_investment_research
    from sqlalchemy import inspect
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as conn:
        _m138_investment_research(conn)
        _m138_investment_research(conn)
    assert {"research_runs", "research_artifacts", "research_deliveries", "research_notification_outbox"} <= set(inspect(engine).get_table_names())
    engine.dispose()


@pytest.mark.parametrize("agent_cls", [PremarketOutlookAgent, IntradayMonitorAgent])
def test_event_evidence_escalates_to_bounded_debate(host, agent_cls, monkeypatch):
    engine, model = host
    agent = agent_cls(event_only=False) if agent_cls is IntradayMonitorAgent else agent_cls()
    configure(engine, agent.name, max_feedback=0)
    async def with_events(context):
        data = await collected(context)
        data["signal_pack"].news.items = [{"title": "QA material announcement", "source": "fixture"}]
        return data
    monkeypatch.setattr(agent, "collect", with_events)
    result = asyncio.run(agent.run(make_context()))
    assert result.raw_data["result_status"] == "ready"
    assert model.calls["bull"] == model.calls["bear"] == 1
    assert result.raw_data["usage"]["feedback"] == 0


def test_ambiguous_cross_market_single_symbol_is_rejected_without_mutation(host):
    agent = IntradayMonitorAgent()
    context = make_context(keys=((MarketCode.CN, "12345"), (MarketCode.US, "12345")))
    original = list(context.watchlist)
    with pytest.raises(ValueError, match="unambiguous"):
        asyncio.run(agent.run_single(context, "12345"))
    assert context.watchlist == original


@pytest.mark.parametrize("success", [True, False])
def test_duplicate_delivery_never_retries_sent_or_unknown_acknowledgement(host, monkeypatch, success):
    engine, _ = host
    agent = DailyReportAgent()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, "collect", collected)
    context = make_context()
    context.suppress_notify = False
    context.notifier.notify_with_result.return_value = {"success": success}
    asyncio.run(agent.run(context))
    asyncio.run(agent.run(context))
    assert context.notifier.notify_with_result.await_count == 1
    with Session(engine) as db:
        assert db.scalars(select(ResearchOutbox)).one().status == ("sent" if success else "unknown")


def test_concurrent_outbox_claims_allow_one_delivery_per_scope(host):
    engine, _ = host
    with Session(engine) as db, db.begin():
        for run_id in ("first", "second"):
            db.add(ResearchOutbox(run_id=run_id, channel_key="qa", policy_scope="shared", ttl_minutes=30, status="pending"))
    async def claim_both():
        return await asyncio.gather(repository.claim("first", "qa"), repository.claim("second", "qa"))
    assert sorted(asyncio.run(claim_both())) == [False, True]


def test_cancellation_marks_run_without_business_writes(host, monkeypatch):
    engine, _ = host
    agent = DailyReportAgent()
    configure(engine, agent.name)
    async def cancelled(context):
        raise asyncio.CancelledError()
    monkeypatch.setattr(agent, "collect", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run(make_context()))
    with Session(engine) as db:
        assert db.scalars(select(ResearchRun)).one().payload["error"] == "cancelled"
        assert not db.scalars(select(ResearchDelivery)).all()


def test_gateway_meters_each_fallback_and_disables_sdk_retries():
    from src.modules.research.orchestration.gateway import ResearchModelGateway
    options, sent = [], []
    failed = AsyncMock(side_effect=RuntimeError("controlled transport failure"))
    response = SimpleNamespace(model="actual-model", usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{}', tool_calls=[]))])
    succeeded = AsyncMock(return_value=response)
    def candidate(create, name):
        sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        def with_options(**kwargs):
            options.append(kwargs)
            return sdk
        return SimpleNamespace(model=name, client=SimpleNamespace(with_options=with_options))
    primary, secondary = candidate(failed, "failed-model"), candidate(succeeded, "glm-5.3-flash")
    client = SimpleNamespace(_iter_candidates=lambda: [(primary, "qa-primary"), (secondary, "qa-secondary")])
    async def permit():
        sent.append("permit")
    reply = asyncio.run(ResearchModelGateway(client).complete(role="technical", messages=[], tools=[], schema={},
        max_output_tokens=4096, timeout=10, before_attempt=permit))
    assert len(sent) == 2 and all(o["max_retries"] == 0 for o in options)
    assert succeeded.call_args.kwargs["reasoning_effort"] == "low"
    assert reply.model == "actual-model" and reply.usage == {"input_tokens": 12, "output_tokens": 4}
