import asyncio
import json

import pytest

from pan_investment_research import InvestmentResearch, ResearchPolicy, ResearchSignal
from test_research import DirectionalModel, Model, prepared


@pytest.mark.parametrize('fast', [False, True])
def test_cash_changes_only_final_execution_and_never_any_model_input(fast):
    data = prepared()
    data.signals = [ResearchSignal(stock_key='CN:600519', domain='technical', reasons=['breakout'], observed_at=data.as_of)]
    policy = ResearchPolicy(profile='intraday', graph='intraday_fast', specialists=['technical'], debate=False, max_feedback=0) if fast else ResearchPolicy()
    engine, models, results = InvestmentResearch(), [], []
    for funds in [0, 1234567]:
        data.portfolio = {'accounts': [{'name': 'PRIVATE_ACCOUNT_MARKER', 'available_funds': funds}], 'buying_power_verified': True}
        model = DirectionalModel()
        results.append(asyncio.run(engine.run(data, model=model, policy=policy)))
        models.append(model)
    assert all(r.completion == 'complete' for r in results), [r.reason for r in results]
    assert models[0].payloads == models[1].payloads
    assert all('PRIVATE_ACCOUNT_MARKER' not in json.dumps(p) and 'available_funds' not in json.dumps(p) for _, p in models[0].payloads)
    left, right = [r.recommendations[0] for r in results]
    assert (left.outlook, left.action, left.reason) == (right.outlook, right.action, right.reason)
    assert left.execution_status == 'blocked' and right.execution_status == 'conditional'
    assert results[0].artifacts[-1].role == 'execution' and results[0].artifacts[-1].kind == 'rule'
    assert all(not rec.execution_constraints for a in results[0].artifacts if a.kind == 'model' for rec in a.report.recommendations)


def test_invented_cash_rationale_is_rejected_instead_of_published_as_watch():
    class InventedCash(Model):
        async def complete(self, **kwargs):
            reply = await super().complete(**kwargs)
            if kwargs['role'] == 'strategy':
                report = json.loads(reply.content)
                report['recommendations'][0]['reason'] = 'Zero cash, so wait.'
                reply.content = json.dumps(report)
            return reply
    result = asyncio.run(InvestmentResearch().run(prepared(), model=InventedCash()))
    assert result.completion == 'validation_failed' and not result.recommendations
    assert 'account information' in result.reason


def test_execution_preserves_the_final_decision_rationale():
    class FinalReason(Model):
        async def complete(self, **kwargs):
            reply = await super().complete(**kwargs)
            if kwargs['role'] == 'decision':
                report = json.loads(reply.content)
                report['recommendations'][0]['reason'] = 'Final synthesis of verified market evidence'
                reply.content = json.dumps(report)
            return reply
    result = asyncio.run(InvestmentResearch().run(prepared(), model=FinalReason()))
    assert result.completion == 'complete', result.reason
    assert result.recommendations[0].reason == 'Final synthesis of verified market evidence'


@pytest.mark.parametrize('profile', ['premarket', 'postmarket'])
def test_session_notes_cover_each_stock_and_have_evidence(profile):
    result = asyncio.run(InvestmentResearch().run(prepared(('CN:600519', 'CN:002594')), model=Model(), policy=ResearchPolicy(profile=profile)))
    assert result.completion == 'complete', result.reason
    notes = next(a for a in result.artifacts if a.role == 'strategy').report.session_notes
    assert len(notes) == 2 and all(n.evidence_ids for n in notes)
    if profile == 'premarket':
        assert all(n.upside and n.downside for n in notes)
    else:
        assert all(n.prior_verdict == 'no_prior' for n in notes)


def test_postmarket_cannot_claim_to_verify_a_nonexistent_morning_plan():
    class Hindsight(Model):
        async def complete(self, **kwargs):
            reply = await super().complete(**kwargs)
            if kwargs['role'] == 'strategy':
                report = json.loads(reply.content)
                report['session_notes'][0]['prior_verdict'] = 'confirmed'
                reply.content = json.dumps(report)
            return reply
    result = asyncio.run(InvestmentResearch().run(prepared(), model=Hindsight()))
    assert result.completion == 'validation_failed' and 'absent prior' in result.reason


def test_tool_continuation_refreshes_exact_citation_ids_in_the_model_contract():
    from test_research import Tools
    data = prepared()
    detail = next(e for e in data.evidence if e.domain == 'events').model_copy(update={'id': 'CN:600519/events/detail'})
    class DetailTools(Tools):
        async def execute(self, *args, **kwargs):
            return [detail]
    class CitationModel(Model):
        async def complete(self, **kwargs):
            # The prompt sent over the real gateway must match the current schema,
            # including IDs discovered after a read-only tool call.
            assert json.dumps(kwargs['schema'], ensure_ascii=False) in kwargs['messages'][0]['content']
            reply = await super().complete(**kwargs)
            if kwargs['role'] == 'events' and not reply.tool_calls:
                assert detail.id in kwargs['schema']['$defs']['Claim']['properties']['evidence_ids']['items']['enum']
                report = json.loads(reply.content)
                report['claims'][0]['evidence_ids'] = [detail.id]
                reply.content = json.dumps(report)
            return reply
    result = asyncio.run(InvestmentResearch().run(data, model=CitationModel(tools=True), tools=DetailTools(data)))
    assert result.completion == 'complete', result.reason
    events = next(a for a in result.artifacts if a.role == 'events')
    assert events.report.claims[0].evidence_ids == [detail.id]


def test_risk_receives_capture_limits_and_previous_feedback_for_plan_revision():
    class RiskRevision(Model):
        async def complete(self, **kwargs):
            if kwargs['role'] == 'risk':
                prompt = kwargs['messages'][0]['content']
                assert 'they cannot fetch a missing announcement' in prompt
                assert 'request revise to remove unsupported causal certainty' in prompt
                if self.calls['risk']:
                    payload = json.loads(kwargs['messages'][1]['content'])
                    assert payload['issues'][0]['question'] == 'Verify timing'
            return await super().complete(**kwargs)
    result = asyncio.run(InvestmentResearch().run(prepared(), model=RiskRevision(risk_feedback='revise')))
    assert result.completion == 'complete', result.reason
    assert result.usage['feedback'] == 1
