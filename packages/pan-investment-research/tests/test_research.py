import asyncio
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from pan_investment_research import (
    Evidence, InvestmentResearch, ModelReply, ResearchInput, ResearchPolicy, Security,
)
from pan_investment_research.budget import Budget, BudgetExceeded


def prepared(keys=("CN:600519",)):
    now = datetime.now(timezone.utc)
    return ResearchInput(run_id="test-run", as_of=now,
        securities=[Security(key=k, price=100, quote_expires_at=now + timedelta(hours=1), held_quantity=0) for k in keys],
        evidence=[Evidence(id=f"{k}/{d}", stock_key=k, domain=d, source="fixture",
                           observed_at=now, available_at=now, content={"fact": f"{d}: recorded observation"})
                  for k in keys for d in ("technical", "flow", "events")])


class Model:
    def __init__(self, *, manager_feedback=False, risk_feedback=None, tools=False, malformed=False, mutate=False):
        self.calls = Counter()
        self.finished = []
        self.payloads = []
        self.manager_feedback = manager_feedback
        self.risk_feedback = risk_feedback
        self.tools = tools
        self.malformed = malformed
        self.mutate = mutate

    async def complete(self, *, role, messages, before_attempt, **kwargs):
        await before_attempt()
        self.calls[role] += 1
        payload = json.loads(messages[1]["content"])
        self.payloads.append((role, payload))
        report = {"summary": f"{role}: evidence-based finding"}
        if role in {"technical", "flow", "events"}:
            await asyncio.sleep({"technical": .003, "flow": .001, "events": .002}[role])
            inp = payload["input"]
            if self.tools and role == "events" and self.calls[role] == 1:
                return ModelReply("", "script", tool_calls=[{"id": "query-1", "type": "function",
                    "function": {"name": "details", "arguments": "{}"}}])
            report["claims"] = [{"id": "local", "stock_key": s["key"],
                "statement": f"{role} finding v{self.calls[role]}",
                "evidence_ids": [e["id"] for e in inp["evidence"] if e["stock_key"] == s["key"]][:1]}
                for s in inp["securities"]]
            if self.malformed and self.calls[role] == 1 and role == "events":
                return ModelReply("invalid-json", "script")
        if role == "manager":
            required = {a["role"] for a in payload["reports"].values()} & {"technical", "flow", "events"}
            assert required and required <= set(self.finished)
            report["accepted_claim_ids"] = [c["id"] for a in payload["reports"].values() for c in a["report"]["claims"]]
            if self.manager_feedback and self.calls[role] == 1:
                report.update(verdict="evidence_needed", issues=[{
                    "stock_key": "CN:600519", "domain": "events", "question": "Is this catalyst already stale?"}])
        if role in {"strategy", "decision"}:
            report["recommendations"] = payload["plan"] if role == "decision" else [
                {"stock_key": s["key"], "outlook": "neutral", "confidence": .5, "action": "watch", "reason": "Wait for confirmation",
                 "evidence_ids": [e["id"] for e in payload["input"]["evidence"] if e["stock_key"] == s["key"]][:1]}
                for s in payload["input"]["securities"]]
            if self.mutate and role == "decision":
                report["recommendations"][0].update(action="buy", triggers=["confirmed"], invalidations=["failed"])
        if role == "strategy" and "session: intraday" not in messages[0]["content"]:
            report["session_notes"] = [{"stock_key": target["key"], "summary": "Controlled session attribution and focus",
                "upside": "Resistance confirms -> reassess the opportunity", "downside": "Support fails -> reassess risk",
                "prior_verdict": "no_prior", "evidence_ids": [e["id"] for e in payload["input"]["evidence"] if e["stock_key"] == target["key"]][:1]}
                for target in payload["input"]["securities"]]
        if role == "risk":
            report["verdict"] = "pass"
            if self.risk_feedback and self.calls[role] == 1:
                report["verdict"] = self.risk_feedback
                report["issues"] = [{"stock_key": "CN:600519", "domain": "events", "question": "Verify timing"}]
        self.finished.append(role)
        return ModelReply(json.dumps(report), "script", usage={"input_tokens": 10, "output_tokens": 5})


class Tools:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    def schemas(self, role):
        return [{"type": "function", "function": {"name": "details", "description": "Read source details", "parameters": {"type": "object"}}}]

    async def execute(self, name, arguments, *, stock_keys):
        self.calls += 1
        return [e for e in self.data.evidence if e.domain == "events" and e.stock_key in stock_keys]


class DirectionalModel(Model):
    def __init__(self, outlook="bullish", action="buy", review=False, reject=False):
        super().__init__()
        self.outlook, self.action, self.review, self.reject = outlook, action, review, reject

    async def complete(self, *, role, **kwargs):
        reply = await super().complete(role=role, **kwargs)
        report = json.loads(reply.content)
        if role == "strategy":
            for rec in report["recommendations"]:
                rec.update(outlook=self.outlook, confidence=.8, action=self.action, review_required=self.review,
                    reason="Market evidence supports this conditional view", triggers=["Confirm price and volume"],
                    invalidations=["Trend breaks"])
        if role == "risk" and self.reject:
            report["verdict"] = "reject"
        reply.content = json.dumps(report)
        return reply


def run(model=None, data=None, policy=None, tools=None):
    return asyncio.run(InvestmentResearch().run(data or prepared(), model=model or Model(), policy=policy, tools=tools))


def test_complete_nine_role_research_and_separate_usage():
    model = Model()
    result = run(model)
    assert result.status == "ready", result.reason
    assert len(result.artifacts) == 10
    assert result.usage["attempts"] == 9
    assert result.usage["input_tokens"] == 90
    assert set(model.calls) == {"technical", "flow", "events", "bull", "bear", "manager", "strategy", "risk", "decision"}


def test_tool_loop_waits_for_terminal_expert_before_manager():
    data, model = prepared(), Model(tools=True)
    tools = Tools(data)
    result = run(model, data, tools=tools)
    assert result.status == "ready", result.reason
    assert tools.calls == 1 and model.calls["events"] == 2
    assert model.calls["manager"] == 1 and result.usage["attempts"] == 10


def test_rejected_tool_arguments_use_one_shared_repair_and_cannot_read_more_evidence():
    class RejectedTools(Tools):
        async def execute(self, name, arguments, *, stock_keys):
            self.calls += 1
            raise ValueError("out-of-scope target")

    class CheckingModel(Model):
        async def complete(self, *, role, tools, **kwargs):
            if role == "events" and self.calls[role]:
                assert tools == []
                assert "no evidence retrieved" in kwargs["messages"][-1]["content"]
            return await super().complete(role=role, **kwargs)

    data, model = prepared(), CheckingModel(tools=True)
    tools = RejectedTools(data)
    result = run(model, data, tools=tools)
    assert result.completion == "complete", result.reason
    assert tools.calls == 1 and model.calls["events"] == 2
    assert result.usage["tools"] == 1 and result.usage["attempts"] == 10


def test_targeted_counterevidence_replaces_stale_claims():
    model = Model(manager_feedback=True)
    result = run(model)
    assert result.status == "ready", result.reason
    assert model.calls["events"] == 2
    assert model.calls["technical"] == model.calls["flow"] == 1
    last = [p for role, p in model.payloads if role == "manager"][-1]
    assert "0/0/events" not in last["reports"]
    assert "0/1/events" in last["reports"]
    assert result.usage["feedback"] == 1
    patches = [p for role, p in model.payloads if role == "events"]
    assert all(e["domain"] == "events" for e in patches[-1]["input"]["evidence"])


@pytest.mark.parametrize("feedback", ["revise", "evidence_needed"])
def test_risk_feedback_revalidates_plan_without_restarting_team(feedback):
    model = Model(risk_feedback=feedback)
    result = run(model)
    assert result.status == "ready", result.reason
    assert model.calls["strategy"] == model.calls["risk"] == 2
    assert model.calls["technical"] == 1
    assert model.calls["events"] == (2 if feedback == "evidence_needed" else 1)


def test_decision_cannot_override_approved_risk_boundaries():
    result = run(Model(mutate=True))
    assert result.status == "review"
    assert "approved plan" in result.reason
    assert result.completion == "validation_failed"
    assert all(r.review_required and r.execution_status == "blocked" for r in result.recommendations)


def test_format_repair_is_metered_once():
    result = run(Model(malformed=True))
    assert result.status == "ready", result.reason
    assert result.usage["attempts"] == 10


@pytest.mark.parametrize("persistent", [False, True])
def test_multi_stock_coverage_uses_one_bounded_repair_without_publishing_a_partial_plan(persistent):
    class MissingTarget(Model):
        async def complete(self, *, role, **kwargs):
            reply = await super().complete(role=role, **kwargs)
            if role == "strategy" and (persistent or self.calls[role] == 1):
                body = json.loads(reply.content)
                body["recommendations"].pop()
                reply.content = json.dumps(body)
            return reply

    model = MissingTarget()
    result = run(model, prepared(("CN:600519", "CN:002594", "CN:300750")))
    assert model.calls["strategy"] == 2
    if persistent:
        assert result.completion == "validation_failed" and not result.recommendations
        assert "exactly once" in result.reason and model.calls["risk"] == 0
    else:
        assert result.completion == "complete" and len(result.recommendations) == 3
        assert model.calls["risk"] == model.calls["decision"] == 1


def test_exhausted_budget_never_turns_unreviewed_plan_into_ready():
    result = run(policy=ResearchPolicy(max_attempts=5))
    assert result.status == "review"
    assert result.usage["attempts"] <= 5
    assert all(r.review_required for r in result.recommendations)


def test_feedback_budget_is_shared_across_research_and_risk():
    result = run(Model(manager_feedback=True, risk_feedback="revise"))
    assert result.status == "review"
    assert result.usage["feedback"] == 1


def test_multi_group_and_same_symbol_in_different_markets():
    data = prepared(("CN:SAME", "US:SAME"))
    model = Model()
    result = run(model, data, ResearchPolicy(max_group_size=1, max_attempts=28, max_total_tokens=200000))
    assert result.status == "ready", result.reason
    assert len(result.recommendations) == 2
    assert model.calls["strategy"] == model.calls["risk"] == model.calls["decision"] == 1
    assert result.usage["attempts"] == 15


def test_independent_concurrent_runs_share_no_messages_or_budget():
    async def both():
        engine = InvestmentResearch()
        return await asyncio.gather(engine.run(prepared(("CN:ONE",)), model=Model()),
                                    engine.run(prepared(("US:TWO",)), model=Model()))
    first, second = asyncio.run(both())
    assert first.status == second.status == "ready"
    assert first.recommendations[0].stock_key == "CN:ONE"
    assert second.recommendations[0].stock_key == "US:TWO"
    assert first.usage["attempts"] == second.usage["attempts"] == 9


def test_future_evidence_and_duplicate_security_are_rejected():
    data = prepared().model_dump(mode="json")
    data["evidence"][0]["available_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="unavailable"):
        ResearchInput.model_validate(data)
    data = prepared().model_dump(mode="json")
    data["securities"] *= 2
    with pytest.raises(ValueError, match="unique"):
        ResearchInput.model_validate(data)


def test_atomic_budget_does_not_oversubscribe_parallel_attempts():
    async def exercise():
        budget = Budget(ResearchPolicy(max_attempts=3))
        outcomes = await asyncio.gather(*(budget.attempt() for _ in range(10)), return_exceptions=True)
        assert budget.attempts == 3
        assert sum(isinstance(x, BudgetExceeded) for x in outcomes) == 7
    asyncio.run(exercise())


def test_tiny_token_budget_stops_before_provider_send():
    result = run(policy=ResearchPolicy(max_total_tokens=1000))
    assert result.status == "review"
    assert result.usage["attempts"] == 0


def test_light_intraday_profile_keeps_strategy_risk_and_decision():
    model = Model()
    result = run(model, policy=ResearchPolicy(profile="intraday", specialists=["technical"], debate=False, max_attempts=10))
    assert result.status == "ready", result.reason
    assert result.usage["attempts"] == 5
    assert set(model.calls) == {"technical", "manager", "strategy", "risk", "decision"}


def test_output_truncation_is_budget_review_without_wasting_format_repair():
    class Truncated(Model):
        async def complete(self, *, before_attempt, **kwargs):
            await before_attempt()
            return ModelReply("", "script", usage={"output_tokens": 4096}, finish_reason="length")
    result = run(Truncated(), policy=ResearchPolicy(specialists=["technical"], debate=False))
    assert result.status == "review"
    assert result.usage["attempts"] == 1
    assert result.usage["output_tokens"] == 4096
    assert "output exhausted" in result.reason


def test_roles_only_receive_schema_fields_they_can_author():
    class SchemaChecking(Model):
        async def complete(self, *, role, schema, **kwargs):
            assert ("recommendations" in schema["properties"]) == (role in {"strategy", "decision"})
            assert ("accepted_claim_ids" in schema["properties"]) == (role == "manager")
            for name in ("Claim", "Issue", "Recommendation"):
                if name in schema["$defs"]:
                    expected = ["CN:600519", "*"] if name == "Issue" else ["CN:600519"]
                    assert schema["$defs"][name]["properties"]["stock_key"]["enum"] == expected
            assert ("Recommendation" in schema["$defs"]) == (role in {"strategy", "decision"})
            assert ("Claim" in schema["$defs"]) == (role in {"technical", "flow", "events", "bull", "bear"})
            return await super().complete(role=role, **kwargs)
    assert run(SchemaChecking()).status == "ready"


def test_intraday_plan_revision_reserves_only_its_three_remaining_model_calls():
    model = Model(tools=True, risk_feedback="revise")
    policy = ResearchPolicy(profile="intraday", specialists=["technical", "events"],
        debate=True, max_attempts=12, max_feedback=1)
    data = prepared()
    result = run(model, data, policy, Tools(data))
    assert result.completion == "complete", result.reason
    assert result.usage["feedback"] == 1 and result.usage["attempts"] == 11
    assert model.calls["strategy"] == model.calls["risk"] == 2
    assert model.calls["decision"] == 1


def test_expert_data_limitations_can_be_adjudicated_without_blocking_the_graph():
    class LimitedFlow(Model):
        async def complete(self, *, role, **kwargs):
            reply = await super().complete(role=role, **kwargs)
            if role == "flow":
                report = json.loads(reply.content)
                report["issues"] = [{"stock_key": "CN:600519", "domain": "flow", "question": "Daily flow detail unavailable"}]
                reply.content = json.dumps(report)
            return reply
    result = run(LimitedFlow())
    assert result.status == "ready" and result.usage["feedback"] == 0


def test_group_wide_issue_can_request_evidence_without_expanding_target_scope():
    class GroupIssue(Model):
        async def complete(self, *, role, **kwargs):
            reply = await super().complete(role=role, **kwargs)
            if role == "risk" and self.calls[role] == 1:
                body = json.loads(reply.content)
                body["issues"][0]["stock_key"] = "*"
                reply.content = json.dumps(body)
            return reply
    data = prepared(("CN:600519", "CN:002594", "CN:300750"))
    model = GroupIssue(risk_feedback="evidence_needed")
    result = run(model, data, ResearchPolicy(max_attempts=25, max_total_tokens=750000))
    assert result.completion == "complete", result.reason
    assert {r.stock_key for r in result.recommendations} == {s.key for s in data.securities}
    assert model.calls["events"] == model.calls["manager"] == 2


def test_unknown_position_cannot_authorize_a_sell_direction():
    from pan_investment_research import Recommendation
    from pan_investment_research.engine import validate_plan
    data = prepared()
    data.securities[0].held_quantity = None
    plan = [Recommendation(stock_key="CN:600519", outlook="bearish", confidence=.7, action="sell", reason="unverified", evidence_ids=[data.evidence[0].id],
        triggers=["confirmed"], invalidations=["failed"])]
    assert "absent position" in ";".join(validate_plan(data, plan))


def test_deadline_review_always_explains_why_research_stopped():
    class Slow(Model):
        async def complete(self, *, before_attempt, **kwargs):
            await before_attempt()
            await asyncio.sleep(.1)
    result = run(Slow(), policy=ResearchPolicy(deadline_seconds=.01, specialists=["technical"], debate=False))
    assert result.status == "review" and "deadline" in result.reason.lower()
    assert all(r.reason and r.review_required for r in result.recommendations)


def test_risk_can_verify_only_plan_citations_without_truncated_source_facts():
    data, model = prepared(), Model()
    data.evidence[0].content = {"document": "x" * 1000, "verified_level": 123.45}
    result = run(model, data)
    assert result.status == "ready"
    payload = next(p for role, p in model.payloads if role == "risk")
    assert len(payload["cited_evidence"]) == 1
    assert payload["cited_evidence"][0]["id"] == data.evidence[0].id
    assert payload["cited_evidence"][0]["content"]["verified_level"] == 123.45
    assert payload["cited_evidence"][0]["content"]["document"] == "x" * 1000


@pytest.mark.parametrize(("outlook", "action"), [
    ("bullish", "buy"), ("bullish", "add"), ("bullish", "hold"),
    ("bearish", "reduce"), ("bearish", "sell"), ("bearish", "avoid"), ("neutral", "watch"),
])
def test_market_views_and_all_seven_actions_survive_zero_cash(outlook, action):
    data = prepared()
    data.portfolio = {"accounts": [{"available_funds": 0}]}
    if action in {"add", "hold", "reduce", "sell"}:
        data.securities[0].held_quantity = 100
    result = run(DirectionalModel(outlook, action), data)
    assert result.completion == "complete", result.reason
    rec = result.recommendations[0]
    assert (rec.outlook, rec.action, rec.confidence) == (outlook, action, .8)
    if action in {"buy", "add"}:
        assert rec.execution_status == "blocked" and "insufficient_funds" in rec.execution_constraints
    if action in {"reduce", "sell"}:
        assert "sellable_quantity_unverified" in rec.execution_constraints
    assert rec.quantity is None


def test_review_and_stale_quote_block_execution_without_overwriting_bullish_buy():
    data = prepared()
    data.securities[0].quote_expires_at = data.as_of - timedelta(seconds=1)
    result = run(DirectionalModel(review=True), data)
    rec = result.recommendations[0]
    assert result.status == "review" and result.completion == "complete"
    assert rec.outlook == "bullish" and rec.action == "buy"
    assert {"review_required", "quote_unavailable_or_expired"} <= set(rec.execution_constraints)
    assert rec.execution_status == "blocked" and rec.quantity is None


def test_verified_buying_power_changes_execution_only_and_specialists_never_receive_accounts():
    data = prepared()
    model = DirectionalModel()
    limited = run(model, data)
    assert all(not p["input"]["portfolio"] for role, p in model.payloads if role in {"technical", "flow", "events"})
    data.portfolio = {"accounts": [{"available_funds": 100000}], "buying_power_verified": True}
    funded = run(DirectionalModel(), data)
    assert limited.recommendations[0].outlook == funded.recommendations[0].outlook == "bullish"
    assert limited.recommendations[0].action == funded.recommendations[0].action == "buy"
    assert limited.recommendations[0].execution_status == "blocked"
    assert funded.recommendations[0].execution_status == "conditional"


def test_risk_rejection_retains_only_a_blocked_unapproved_draft():
    result = run(DirectionalModel(reject=True))
    assert result.completion == "risk_rejected" and result.status == "review"
    rec = result.recommendations[0]
    assert rec.outlook == "bullish" and rec.action == "buy"
    assert rec.review_required and "research_incomplete" in rec.execution_constraints


def test_timeout_after_strategy_keeps_its_view_but_cannot_publish_a_plan():
    class SlowRisk(DirectionalModel):
        async def complete(self, *, role, **kwargs):
            if role == "risk":
                await asyncio.sleep(.2)
            return await super().complete(role=role, **kwargs)
    result = run(SlowRisk(), policy=ResearchPolicy(deadline_seconds=.05))
    assert result.completion == "timeout" and result.status == "review"
    assert result.recommendations[0].action == "buy"
    assert result.recommendations[0].execution_status == "blocked"
    early = run(policy=ResearchPolicy(max_attempts=1))
    assert early.completion == "budget_exhausted" and early.recommendations == []


def test_confidence_must_be_finite_and_blocked_results_never_authorize_quantities():
    from pan_investment_research import Recommendation
    for values in ({"confidence": 1.1}, {"confidence": float("nan")}, {"confidence": .8, "quantity": 10, "execution_status": "blocked"}):
        with pytest.raises(ValueError):
            Recommendation(stock_key="CN:600519", outlook="bullish", action="buy", reason="test", **values)


def test_quote_expiring_during_risk_tightens_execution_without_losing_research():
    class DelayedRisk(DirectionalModel):
        async def complete(self, *, role, **kwargs):
            if role == "risk":
                await asyncio.sleep(.08)
            return await super().complete(role=role, **kwargs)
    data = prepared()
    data.securities[0].quote_expires_at = data.as_of + timedelta(seconds=.04)
    data.portfolio = {"buying_power_verified": True}
    result = run(DelayedRisk(), data)
    assert result.completion == "complete", result.reason
    assert result.recommendations[0].action == "buy"
    assert result.recommendations[0].execution_status == "blocked"
    assert "quote_unavailable_or_expired" in result.recommendations[0].execution_constraints


@pytest.mark.parametrize('action', ['watch', 'hold', 'avoid'])
def test_non_order_advice_is_not_blocked_by_model_account_notes(action):
    from pan_investment_research import Recommendation
    from pan_investment_research.engine import constrain_execution
    data = prepared()
    before = Recommendation(stock_key=data.securities[0].key, outlook='neutral', confidence=.7,
        action=action, reason='wait', execution_status='blocked', execution_constraints=['insufficient_funds'])
    rec = constrain_execution(data, [before])[0]
    assert rec.action == action and rec.execution_status == 'not_applicable' and rec.quantity is None
    assert rec.execution_constraints == before.execution_constraints and before.execution_status == 'blocked'
