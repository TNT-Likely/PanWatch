"""Replay session failures with the ordinary agent entry and isolated persistence."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.automation.daily_report import DailyReportAgent
from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.modules.automation.premarket_outlook import PremarketOutlookAgent
from src.modules.research.orchestration import repository, service
from src.modules.research.orchestration.gateway import ResearchProviderError, ResearchModelGateway
from src.modules.research.orchestration.models import ResearchRun, ResearchDelivery, ResearchOutbox
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.models import AgentPredictionOutcome, AnalysisHistory, StockSuggestion
from test_investment_research_integration import collected, configure, host, make_context, scripts


@pytest.mark.parametrize("agent_cls", [DailyReportAgent, PremarketOutlookAgent, IntradayMonitorAgent])
@pytest.mark.parametrize("failure", ["all_models_down", "tool_json_in_body"])
def test_all_sessions_save_captured_facts_when_the_model_cannot_finish(host, monkeypatch, agent_cls, failure):
    from pan_investment_research import ModelReply

    class BrokenModel:
        async def complete(self, *, before_attempt, tools, **kwargs):
            assert tools == []
            await before_attempt()
            if failure == "all_models_down":
                raise ResearchProviderError("Provider failed")
            return ModelReply('{"name":"read_technical_details","arguments":{"stock_keys":["CN:600519"]}}', "fixture")

    engine, _ = host
    agent = agent_cls()
    configure(engine, agent.name, budget_enabled=False)
    monkeypatch.setattr(agent, "collect", collected)
    monkeypatch.setattr(service, "ResearchModelGateway", lambda _: BrokenModel())
    result = asyncio.run(agent.run(make_context()))
    assert result.raw_data["completion"] == "degraded"
    assert "行情：100.00" in result.content
    expected = "model_unavailable" if failure == "all_models_down" else "validation_failed"
    assert result.raw_data["research_report"]["runtime"]["group_failures"] == {"CN:600519": expected}
    with Session(engine) as db:
        assert db.scalars(select(AnalysisHistory)).one().raw_data["completion"] == "degraded"
        assert not db.scalars(select(StockSuggestion)).all()
        assert not db.scalars(select(AgentPredictionOutcome)).all()


@pytest.mark.parametrize("count", [8, 9])
def test_material_signals_stay_scoped_and_peer_group_survives(host, monkeypatch, count):
    class GroupModel(scripts.Model):
        async def complete(self, *, role, messages, before_attempt, **kwargs):
            payload = json.loads(messages[1]["content"])
            if data := payload.get("input"):
                keys = {stock["key"] for stock in data["securities"]}
                assert {signal["stock_key"] for signal in data["signals"]} == keys
                if keys == {"CN:600008"}:
                    await before_attempt()
                    raise ResearchProviderError("Second group unavailable")
            return await super().complete(role=role, messages=messages, before_attempt=before_attempt, **kwargs)

    async def material(context):
        data = await collected(context)
        for pack in data["signal_packs"].values():
            pack.events.items = [{"title": "获证监会批准，重大事项公告", "url": "https://example.invalid/event",
                                  "importance": 2}]
        return data

    engine, _ = host
    agent, model = DailyReportAgent(), GroupModel()
    configure(engine, agent.name, budget_enabled=False)
    monkeypatch.setattr(agent, "collect", material)
    monkeypatch.setattr(service, "ResearchModelGateway", lambda _: model)
    keys = tuple((MarketCode.CN, f"600{i:03}") for i in range(count))
    result = asyncio.run(agent.run(make_context(keys=keys)))
    assert model.calls["strategy"] == model.calls["risk"] == 1
    assert result.raw_data["completion"] == ("complete" if count == 8 else "degraded")
    if count == 9:
        assert result.raw_data["research_report"]["runtime"]["group_failures"] == {"CN:600008": "model_unavailable"}
        assert len(result.raw_data["drafts"]) == 8
        assert all(item["execution_status"] == "blocked" for item in result.raw_data["drafts"])
        assert not result.raw_data["suggestions"]


def test_hard_deadline_applies_when_resource_budget_is_disabled(host, monkeypatch):
    class StalledModel:
        async def complete(self, *, before_attempt, **kwargs):
            await before_attempt()
            await asyncio.Event().wait()

    engine, _ = host
    agent = DailyReportAgent()
    configure(engine, agent.name, budget_enabled=False)
    monkeypatch.setattr(agent, "collect", collected)
    monkeypatch.setattr(service, "ResearchModelGateway", lambda _: StalledModel())
    monkeypatch.setitem(service.SESSION_TIMEOUTS, agent.name, .35)
    result = asyncio.run(asyncio.wait_for(agent.run(make_context()), 2))
    assert result.raw_data["completion"] == "degraded"
    assert "CN:600519" in result.raw_data["research_report"]["runtime"]["group_failures"]
    assert "行情：100.00" in result.content


def test_unavailable_quotes_are_not_reported_as_a_quiet_market(host, monkeypatch):
    engine, model = host
    agent = IntradayMonitorAgent()
    monkeypatch.setattr(agent, "collect", AsyncMock(return_value={}))
    result = asyncio.run(agent.run(make_context()))
    assert result.raw_data["completion"] == "data_unavailable"
    assert result.raw_data["skipped"] is False
    assert result.raw_data["suggestions"] == {}
    assert not model.calls
    with Session(engine) as db:
        assert db.scalars(select(ResearchDelivery)).one().status == "committed"


class ProcessLost(BaseException):
    """A worker exits without executing application error handlers."""


def test_recover_completed_group_and_pending_notice_without_replaying_models(host, monkeypatch):
    engine, model = host
    agent = DailyReportAgent()
    collect = AsyncMock(side_effect=collected)
    monkeypatch.setattr(agent, "collect", collect)
    context = make_context("sch-daily_report-first")
    context.suppress_notify = False
    real_commit, real_deliver = repository.commit, service.deliver
    monkeypatch.setattr(repository, "commit", AsyncMock(side_effect=ProcessLost))

    with pytest.raises(ProcessLost):
        asyncio.run(agent.run(context))
    with Session(engine) as db, db.begin():
        row = db.scalars(select(ResearchRun)).one()
        assert row.payload["groups"]["portfolio"]["completion"] == "complete"
        row.lease_until = 0
    context._trace_id = "sch-daily_report-next"
    calls = dict(model.calls)
    monkeypatch.setattr(repository, "commit", real_commit)
    monkeypatch.setattr(service, "deliver", AsyncMock(side_effect=ProcessLost))
    with pytest.raises(ProcessLost):
        asyncio.run(agent.run(context))
    with Session(engine) as db:
        assert db.scalars(select(ResearchOutbox)).one().status == "pending"
    assert dict(model.calls) == calls and collect.await_count == 1

    monkeypatch.setattr(service, "deliver", real_deliver)
    result = asyncio.run(agent.run(context))
    assert result.raw_data["completion"] == "complete" and result.raw_data["notified"]
    assert asyncio.run(agent.run(context)).raw_data["notified"]
    context.notifier.notify_with_result.assert_awaited_once()
    with Session(engine) as db:
        assert len(db.scalars(select(ResearchDelivery)).all()) == 1
        assert len(db.scalars(select(AgentPredictionOutcome)).all()) == 2
        assert len(db.scalars(select(AnalysisHistory)).all()) == 1


def test_official_gpt_tools_use_responses_and_return_reasoning_state():
    reasoning = {"type": "reasoning", "id": "rs_fixture", "encrypted_content": "fixture"}
    tool_call = {"type": "function_call", "id": "fc_fixture", "call_id": "call_fixture",
                 "name": "read_fixture", "arguments": '{}'}
    items = [SimpleNamespace(**item, model_dump=lambda payload=item, **kwargs: payload)
             for item in (reasoning, tool_call)]
    responses = AsyncMock(side_effect=[
        SimpleNamespace(status="completed", output=items, usage=None, output_text="", model="gpt-6.2-sol"),
        SimpleNamespace(status="completed", output=[], usage=None, output_text='{"summary":"ok"}', model="gpt-6.2-sol"),
    ])
    sdk = SimpleNamespace(responses=SimpleNamespace(create=responses))
    client = SimpleNamespace(model="gpt-6.2-sol", base_url="https://api.openai.com/v1",
                             client=SimpleNamespace(with_options=lambda **kwargs: sdk))
    gateway = ResearchModelGateway(client)
    messages = [{"role": "user", "content": "Read the captured fixture."}]
    kwargs = {"role": "technical", "tools": [{"type": "function", "function": {"name": "read_fixture",
              "parameters": {"type": "object"}}}], "schema": {}, "max_output_tokens": 1024,
              "timeout": 2, "before_attempt": AsyncMock()}
    first = asyncio.run(gateway.complete(messages=messages, **kwargs))
    assert first.tool_calls[0]["id"] == "call_fixture"
    messages += [{"role": "assistant", "content": None, "tool_calls": first.tool_calls, "response_items": first.response_items},
                 {"role": "tool", "tool_call_id": "call_fixture", "content": "captured fact"}]
    result = asyncio.run(gateway.complete(messages=messages, **kwargs))
    assert result.content == '{"summary":"ok"}'
    sent = responses.await_args.kwargs
    assert sent["max_output_tokens"] == 1024 and sent["store"] is False
    assert sent["input"][1:] == [reasoning, tool_call,
        {"type": "function_call_output", "call_id": "call_fixture", "output": "captured fact"}]
