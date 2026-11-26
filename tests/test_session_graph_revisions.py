"""Scoped postmarket risk revisions and time-bounded premarket debate, using scripted providers."""
import asyncio
import json
import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pan_investment_research import InvestmentResearch
from pan_investment_research.budget import Budget
from src.modules.automation.daily_report import DailyReportAgent
from src.modules.research.orchestration import service
from src.modules.research.orchestration.policies import validate_config
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.models import AgentPredictionOutcome, StockSuggestion
from test_investment_research_integration import collected, host, make_context, scripts


class PostmarketModel(scripts.Model):
    def __init__(self, *, outcome="pass"):
        super().__init__(risk_feedback="revise")
        self.outcome = outcome

    async def complete(self, *, role, **kwargs):
        reply = await super().complete(role=role, **kwargs)
        body = json.loads(reply.content)
        if role == "strategy" and self.calls[role] == 2 and self.outcome != "unchanged":
            body["session_notes"][0]["summary"] = "The source confirms the timing only. The cause remains uncertain."
        if role == "risk" and self.outcome == "unspecified":
            body.update(verdict="revise", issues=[])
        if role == "risk" and self.calls[role] == 2:
            if self.outcome == "stalled":
                await asyncio.Event().wait()
            if self.outcome == "revise":
                body.update(verdict="revise", issues=[{"stock_key": "CN:600519", "domain": "events",
                    "question": "Which recorded event establishes its timing?"}])
            elif self.outcome == "reject":
                body["verdict"] = "reject"
        reply.content = json.dumps(body)
        return reply


def test_scoped_revision_rechecks_the_whole_plan_at_the_exact_attempt_cap(host, monkeypatch):
    db_engine, _ = host
    model = PostmarketModel()
    agent = DailyReportAgent(research_graph={"budget_enabled": True, "max_attempts": 7})
    monkeypatch.setattr(agent, "collect", collected)
    monkeypatch.setattr(service, "ResearchModelGateway", lambda _: model)
    context = make_context(keys=((MarketCode.CN, "000001"), (MarketCode.CN, "600519")))
    result = asyncio.run(agent.run(context))

    assert result.raw_data["completion"] == "complete", result.content
    assert result.raw_data["usage"]["attempts"] == 7 and result.raw_data["usage"]["feedback"] == 1
    assert model.calls == {"technical": 1, "events": 1, "manager": 1, "strategy": 2, "risk": 2}
    before, revision = [p for role, p in model.payloads if role == "strategy"]
    assert len(before["input"]["securities"]) == 2
    assert {s["key"] for s in revision["input"]["securities"]} == {"CN:600519"}
    assert {c["stock_key"] for c in revision["accepted_claims"]} == {"CN:600519"}
    assert [n["stock_key"] for n in revision["session_notes"]] == ["CN:600519"]
    first, final = [p for role, p in model.payloads if role == "risk"]
    assert len(final["plan"]) == len(final["session_notes"]) == 2
    assert first["session_notes"][0] == final["session_notes"][0]
    assert first["session_notes"][1] != final["session_notes"][1]
    assert final["issues"] == revision["issues"]
    assert set(final["session_notes"][1]["evidence_ids"]) <= {e["id"] for e in final["cited_evidence"]}
    with Session(db_engine) as db:
        assert len(db.scalars(select(StockSuggestion)).all()) == 2
        assert len(db.scalars(select(AgentPredictionOutcome)).all()) == 4


@pytest.mark.parametrize("model, config, completion, strategies, reviews", [
    (PostmarketModel(), {"max_feedback": 0}, "budget_exhausted", 1, 1),
    (PostmarketModel(), {"budget_enabled": True, "max_attempts": 6}, "budget_exhausted", 1, 1),
    (PostmarketModel(outcome="revise"), {}, "budget_exhausted", 2, 2),
    (PostmarketModel(outcome="reject"), {}, "risk_rejected", 2, 2),
    (scripts.DirectionalModel(reject=True), {}, "risk_rejected", 1, 1),
    (PostmarketModel(outcome="unchanged"), {}, "validation_failed", 2, 1),
    (PostmarketModel(outcome="unspecified"), {}, "validation_failed", 1, 1),
    (scripts.Model(risk_feedback="evidence_needed"), {}, "budget_exhausted", 1, 1),
    (scripts.Model(manager_feedback=True), {}, "budget_exhausted", 0, 0),
])
def test_stable_revision_keeps_each_publication_gate(model, config, completion, strategies, reviews):
    _, policy = validate_config(config, "daily_report")
    result = asyncio.run(InvestmentResearch().run(scripts.prepared(), model=model, policy=policy, finalize_plan=True))
    assert result.completion == completion, result.reason
    assert model.calls["strategy"] == strategies and model.calls["risk"] == reviews
    assert model.calls["technical"] == model.calls["events"] == model.calls["manager"] == 1
    assert not ({"bull", "bear", "flow"} & set(model.calls))
    assert all(r.review_required and r.execution_status == "blocked" for r in result.recommendations)
    assert not any(a.role in {"decision", "execution"} for a in result.artifacts)


def test_revision_cannot_extend_the_shared_deadline():
    _, policy = validate_config({}, "daily_report")
    model = PostmarketModel(outcome="stalled")
    budget = Budget(policy, deadline=time.monotonic() + 1)
    result = asyncio.run(InvestmentResearch().run(scripts.prepared(), model=model, policy=policy, budget=budget,
                                                   finalize_plan=True))
    assert result.completion == "timeout" and model.calls["risk"] == 2
    assert result.usage["feedback"] == 1 and result.recommendations[0].execution_status == "blocked"


@pytest.mark.parametrize("seconds, debates", [(5, 0), (180, 1), (None, 1)])
def test_optional_premarket_debate_leaves_time_for_required_roles(seconds, debates):
    _, policy = validate_config({"template": "premarket_balanced", "debate_on": "always"}, "premarket_outlook")
    model = scripts.Model()
    budget = Budget(policy, deadline=time.monotonic() + seconds if seconds is not None else None)
    result = asyncio.run(InvestmentResearch().run(scripts.prepared(), model=model, policy=policy, budget=budget,
                                                   finalize_plan=True))
    assert result.completion == "complete", result.reason
    assert model.calls["bull"] == model.calls["bear"] == debates
    assert model.calls["manager"] == model.calls["strategy"] == model.calls["risk"] == 1


def test_slow_debate_releases_its_time_and_a_single_side_never_enters_the_ruling(monkeypatch):
    from pan_investment_research import engine

    monkeypatch.setattr(engine, "MIN_DEBATE_TURN_SECONDS", 0)
    class SlowBear(scripts.Model):
        async def complete(self, *, role, **kwargs):
            reply = await super().complete(role=role, **kwargs)
            if role == "bear":
                await asyncio.Event().wait()
            return reply

    _, policy = validate_config({"template": "premarket_balanced", "debate_on": "always"}, "premarket_outlook")
    model = SlowBear()
    budget = Budget(policy, deadline=time.monotonic() + 2)
    result = asyncio.run(InvestmentResearch().run(scripts.prepared(), model=model, policy=policy, budget=budget,
                                                   finalize_plan=True))
    assert result.completion == "complete" and model.calls["bull"] == model.calls["bear"] == 1
    assert model.calls["risk"] == 1 and result.usage["attempts"] == 8
    assert not any(a.role in {"bull", "bear"} for a in result.artifacts)
    ruling = next(payload for role, payload in model.payloads if role == "manager")
    assert not any(a["role"] in {"bull", "bear"} for a in ruling["reports"].values())


@pytest.mark.parametrize("repaired", [False, True])
def test_technical_summary_type_uses_one_scoped_contract_repair(repaired):
    class MisshapenSummary(scripts.Model):
        async def complete(self, *, role, messages, **kwargs):
            reply = await super().complete(role=role, messages=messages, **kwargs)
            if role == "technical":
                assert "summary as one non-empty JSON string" in messages[0]["content"]
                if self.calls[role] == 2:
                    assert "summary as one non-empty JSON string" in messages[-1]["content"]
                if self.calls[role] == 1 or not repaired:
                    body = json.loads(reply.content)
                    body["summary"] = {"malformed": body["summary"]}
                    reply.content = json.dumps(body)
            return reply

    _, policy = validate_config({}, "daily_report")
    model = MisshapenSummary()
    result = asyncio.run(InvestmentResearch().run(scripts.prepared(), model=model, policy=policy, finalize_plan=True))
    assert model.calls["technical"] == 2 and model.calls["risk"] == int(repaired)
    assert result.completion == ("complete" if repaired else "validation_failed")
