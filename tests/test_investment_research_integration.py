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
    monkeypatch.setattr(DailyReportAgent, "_fetch_index_for_market", AsyncMock(return_value=[]))
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
            "engine": mode, "template": "custom_research", "specialists": ["technical", "flow", "events"], "max_attempts": 25,
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


@pytest.mark.parametrize("mode", ["legacy", "shadow"])
def test_retired_engines_cannot_start_or_fall_back_to_single_prompt(host, monkeypatch, mode):
    engine, model = host
    agent = DailyReportAgent()
    configure(engine, agent.name, mode)
    monkeypatch.setattr(agent, "collect", AsyncMock(side_effect=AssertionError("must reject before collection")))
    with pytest.raises(ValueError, match="retired"):
        asyncio.run(agent.run(make_context()))
    assert not model.calls
    with Session(engine) as db:
        assert not db.scalars(select(ResearchRun)).all()


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
    configure(engine, agent.name, max_feedback=0, debate=True, debate_on="material")
    async def with_events(context):
        data = await collected(context)
        data["signal_pack"].news.items = [{"title": "QA material announcement", "source": "fixture", "importance": 2}]
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


@pytest.mark.parametrize("with_tools", [False, True])
def test_gateway_meters_each_fallback_and_disables_sdk_retries(with_tools):
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
    reply = asyncio.run(ResearchModelGateway(client).complete(role="technical", messages=[], tools=[{"type": "function"}] if with_tools else [], schema={},
        max_output_tokens=4096, timeout=10, before_attempt=permit))
    assert len(sent) == 2 and all(o["max_retries"] == 0 for o in options)
    assert succeeded.call_args.kwargs["reasoning_effort"] == "low"
    assert succeeded.call_args.kwargs.get("response_format") == (None if with_tools else {"type": "json_object"})
    assert failed.call_args.kwargs.get("response_format") == (None if with_tools else {"type": "json_object"})
    assert "reasoning_effort" not in failed.call_args.kwargs
    assert reply.model == "actual-model" and reply.usage == {"input_tokens": 12, "output_tokens": 4}


def test_gateway_clamps_max_tokens_to_the_advertised_range():
    import httpx
    from openai import BadRequestError
    from src.modules.research.orchestration.gateway import ResearchModelGateway
    request = httpx.Request("POST", "http://test")
    sent_kwargs, permits = [], []
    response = SimpleNamespace(model="actual-model", usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{}', tool_calls=[]))])
    async def create(**kwargs):
        sent_kwargs.append(kwargs)
        if kwargs.get("max_tokens", 0) > 1024:
            raise BadRequestError("max_tokens参数非法：限制数值范围[1,1024]",
                                  response=httpx.Response(400, request=request), body=None)
        return response
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    def with_options(**kwargs):
        return sdk
    client = SimpleNamespace(_iter_candidates=lambda: [(SimpleNamespace(model="deepseek-v4", client=SimpleNamespace(with_options=with_options)), "qa-only")])
    async def permit():
        permits.append(1)
    reply = asyncio.run(ResearchModelGateway(client).complete(role="technical", messages=[], tools=[], schema={},
        max_output_tokens=16000, timeout=10, before_attempt=permit))
    assert len(permits) == 2 and len(sent_kwargs) == 2
    assert sent_kwargs[0]["max_tokens"] == 16000 and sent_kwargs[1]["max_tokens"] == 1024
    assert reply.model == "actual-model"


def test_gateway_strips_json_mode_when_the_endpoint_rejects_it():
    import httpx
    from openai import BadRequestError
    from src.modules.research.orchestration.gateway import ResearchModelGateway
    request = httpx.Request("POST", "http://test")
    sent_kwargs, permits = [], []
    response = SimpleNamespace(model="actual-model", usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{}', tool_calls=[]))])
    async def create(**kwargs):
        sent_kwargs.append(kwargs)
        if "response_format" in kwargs:
            raise BadRequestError("response_format is not supported by this endpoint",
                                  response=httpx.Response(400, request=request), body=None)
        return response
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    def with_options(**kwargs):
        return sdk
    client = SimpleNamespace(_iter_candidates=lambda: [(SimpleNamespace(model="deepseek-v4", client=SimpleNamespace(with_options=with_options)), "qa-only")])
    async def permit():
        permits.append(1)
    reply = asyncio.run(ResearchModelGateway(client).complete(role="technical", messages=[], tools=[], schema={},
        max_output_tokens=2048, timeout=10, before_attempt=permit))
    assert len(permits) == 2 and len(sent_kwargs) == 2
    assert "response_format" in sent_kwargs[0] and "response_format" not in sent_kwargs[1]
    assert reply.model == "actual-model" and reply.usage == {"input_tokens": 7, "output_tokens": 3}


def test_bullish_buy_survives_review_and_zero_cash_through_storage_and_readback(host, monkeypatch):
    from src.modules.automation import suggestion_pool
    from sqlalchemy.orm import sessionmaker
    engine, _ = host
    monkeypatch.setattr(suggestion_pool, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(service, "ResearchModelGateway", lambda client: scripts.DirectionalModel(review=True))
    agent = DailyReportAgent()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, "collect", collected)
    result = asyncio.run(agent.run(make_context()))
    assert result.raw_data["completion"] == "complete" and result.raw_data["result_status"] == "review"
    assert "**最终结论：** 看多" in result.content and "条件性建议：买入" in result.content
    assert "执行受限" in result.content and "未配置交易账户" in result.content
    latest = suggestion_pool.get_latest_suggestions(["600519"])["CN:600519"]
    assert latest["action"] == "buy" and latest["outlook"] == "bullish"
    assert latest["review_required"] and latest["execution_status"] == "blocked"
    assert latest["confidence"] == .8
    with Session(engine) as db:
        predictions = db.scalars(select(AgentPredictionOutcome)).all()
        assert len(predictions) == 2 and all(p.action == "buy" and p.confidence == .8 for p in predictions)


@pytest.mark.parametrize("after_strategy", [False, True])
def test_incomplete_research_keeps_history_but_never_publishes_fake_watch_predictions(host, monkeypatch, after_strategy):
    engine, _ = host
    agent = DailyReportAgent()
    configure(engine, agent.name, **({} if after_strategy else {"max_attempts": 1}))
    monkeypatch.setattr(agent, "collect", collected)
    monkeypatch.setattr(service, "ResearchModelGateway", lambda client: scripts.DirectionalModel(reject=after_strategy))
    result = asyncio.run(agent.run(make_context()))
    assert result.raw_data["completion"] == ("risk_rejected" if after_strategy else "budget_exhausted")
    assert not result.raw_data["suggestions"]
    assert "研究未完成" in result.content
    if after_strategy:
        assert result.raw_data["drafts"][0]["outlook"] == "bullish"
    else:
        assert result.raw_data["drafts"] == []
        assert "尚无有效投研结论" in result.content
    with Session(engine) as db:
        assert not db.scalars(select(StockSuggestion)).all()
        assert not db.scalars(select(AgentPredictionOutcome)).all()
        assert len(db.scalars(select(AnalysisHistory)).all()) == 1
        assert db.scalars(select(ResearchRun)).one().payload["completion"] != "complete"


def test_english_report_distinguishes_market_view_from_account_execution(host, monkeypatch):
    engine, _ = host
    agent = DailyReportAgent()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, "collect", collected)
    monkeypatch.setattr(service, "ResearchModelGateway", lambda client: scripts.DirectionalModel())
    context = make_context()
    context.report_language = "en-US"
    result = asyncio.run(agent.run(context))
    assert "**Conclusion：** Bullish" in result.content
    assert "Conditional action：Buy" in result.content
    assert "**Execution：** Blocked" in result.content


@pytest.mark.parametrize("agent_cls", [PremarketOutlookAgent, DailyReportAgent])
def test_batch_research_reads_all_targets_with_three_tools_and_one_portfolio_decision(host, monkeypatch, agent_cls):
    import json
    from pan_investment_research import ModelReply
    engine, _ = host

    class BatchModel(scripts.Model):
        async def complete(self, *, role, messages, before_attempt, **kwargs):
            if role in {"technical", "flow", "events"} and not self.calls[role]:
                await before_attempt()
                self.calls[role] += 1
                keys = [s["key"] for s in json.loads(messages[1]["content"])["input"]["securities"]]
                return ModelReply("", "script", tool_calls=[{"id": role, "type": "function", "function": {
                    "name": f"read_{role}_details", "arguments": json.dumps({"stock_keys": keys})}}])
            return await super().complete(role=role, messages=messages, before_attempt=before_attempt, **kwargs)

    model, agent = BatchModel(), agent_cls()
    monkeypatch.setattr(service, "ResearchModelGateway", lambda client: model)
    configure(engine, agent.name)
    monkeypatch.setattr(agent, "collect", collected)
    context = make_context(keys=tuple((MarketCode.CN, key) for key in ("600519", "002594", "300750")))
    result = asyncio.run(agent.run(context))
    assert result.raw_data["completion"] == "complete", result.content
    assert set(result.raw_data["suggestions"]) == {"CN:600519", "CN:002594", "CN:300750"}
    assert result.raw_data["usage"]["tools"] == 3
    assert model.calls["strategy"] == model.calls["risk"] == model.calls["decision"] == 1
    with Session(engine) as db:
        assert len(db.scalars(select(StockSuggestion)).all()) == 3
        assert len(db.scalars(select(AgentPredictionOutcome)).all()) == 6
        assert len(db.scalars(select(AnalysisHistory)).all()) == 1


def test_batch_details_rejects_an_entire_mixed_scope_request():
    from src.modules.research.orchestration.gateway import EvidenceTools
    data = scripts.prepared(("CN:600519", "US:600519"))
    tools = EvidenceTools(data.evidence)
    found = asyncio.run(tools.execute("read_technical_details", {"stock_keys": ["CN:600519", "US:600519"]},
        stock_keys=["CN:600519", "US:600519"]))
    assert {e.stock_key for e in found} == {"CN:600519", "US:600519"}
    assert all(e.domain == "technical" for e in found)
    for keys in ([], ["CN:600519", "US:600519"], ["CN:600519"] * 2, "CN:600519", [None]):
        with pytest.raises(ValueError, match="scope"):
            asyncio.run(tools.execute("read_technical_details", {"stock_keys": keys}, stock_keys=["CN:600519"]))


def test_multi_target_default_capacities_preserve_explicit_global_limits():
    _, default = service.validate_config({}, "daily_report")
    sized = service.scope_policy(default, {}, 3)
    assert sized.max_attempts == default.max_attempts  # Share roles within the group.
    assert sized.max_tools == default.max_tools  # Batch reads, not a per-stock tool loop.
    assert sized.max_output_tokens >= default.max_output_tokens  # Base already sits at the cap.
    assert sized.max_input_chars >= default.max_input_chars  # Base already sits at the cap.
    assert sized.max_total_tokens > default.max_total_tokens
    caps = {"max_total_tokens": 9000, "max_input_chars": 1000, "max_output_tokens": 128, "max_tools": 0}
    _, explicit = service.validate_config(caps, "daily_report")
    scoped = service.scope_policy(explicit, caps, 30)
    assert all(getattr(scoped, key) == value for key, value in caps.items())
    assert service.scope_policy(default, {}, 30).max_total_tokens <= 1000000


def test_notification_starts_with_the_same_actions_saved_in_the_report(host, monkeypatch):
    from src.platform.notifications.notifier import sanitize_for_telegram
    engine, _ = host
    agent = DailyReportAgent()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, 'collect', collected)
    context = make_context('action-notification')
    context.suppress_notify = False
    result = asyncio.run(agent.run(context))
    title, message = context.notifier.notify_with_result.call_args.args
    assert message == result.content and title == result.title
    assert message.index('## 操作建议') < message.index('## 大盘概览')
    assert '操作建议：**' + result.raw_data['suggestions']['CN:600519']['action_label'] + '**' in message
    plain = sanitize_for_telegram(message)
    assert plain.index('操作建议') < plain.index('大盘概览')


@pytest.mark.parametrize('agent_cls', [DailyReportAgent, PremarketOutlookAgent, IntradayMonitorAgent])
def test_missing_graph_configuration_uses_session_graph_without_legacy_prompt(host, monkeypatch, agent_cls):
    _, model = host
    agent = agent_cls()
    monkeypatch.setattr(agent, 'collect', collected)
    result = asyncio.run(agent.run(make_context('no-legacy-' + agent.name)))
    assert result.raw_data['engine'] == 'langgraph'
    if agent.name == 'intraday_monitor':
        assert result.raw_data['completion'] == 'skipped' and not model.calls
    else:
        assert result.raw_data['completion'] == 'complete', result.content
    with pytest.raises(NotImplementedError):
        agent.build_prompt({}, make_context())


def test_prior_plan_is_same_market_day_before_cutoff_and_excludes_account_prose(host):
    from datetime import timedelta
    from src.modules.research.orchestration.evidence import prior_plan
    engine, _ = host
    now = datetime.now(timezone.utc)
    day = now.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date().isoformat()
    with Session(engine) as db, db.begin():
        db.add(AnalysisHistory(agent_name='premarket_outlook', stock_symbol='*', analysis_date=day,
            content='PRIVATE_CASH_PROSE', created_at=now - timedelta(hours=1), updated_at=now - timedelta(hours=1), raw_data={
                'suggestions': {'CN:600519': {'outlook': 'bullish', 'action': 'buy', 'reason': 'PRIVATE_CASH_PROSE',
                    'execution_constraints': ['insufficient_funds'], 'triggers': ['Break resistance'], 'invalidations': ['Break support']}}}))
    result = asyncio.run(prior_plan('CN:600519', now, 'Asia/Shanghai'))
    assert result['action'] == 'buy' and result['triggers'] == ['Break resistance']
    assert 'PRIVATE_CASH_PROSE' not in str(result) and 'execution_constraints' not in result
    assert asyncio.run(prior_plan('HK:600519', now, 'Asia/Shanghai')) is None
    with Session(engine) as db, db.begin():
        row = db.scalars(select(AnalysisHistory)).one()
        row.updated_at = now + timedelta(hours=1)
    assert asyncio.run(prior_plan('CN:600519', now, 'Asia/Shanghai')) is None
