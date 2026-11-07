import asyncio
import json

import pytest

from pan_investment_research import InvestmentResearch, ResearchPolicy
from pan_investment_research.prompts import ROLES, instructions
from test_research import Model, prepared


@pytest.mark.parametrize('profile', ['premarket', 'postmarket', 'intraday'])
@pytest.mark.parametrize('role', list(ROLES))
def test_notes_authoring_guidance_matches_the_role_and_session_schema(role, profile):
    text = instructions(role, profile)
    allowed = role == 'strategy' and profile != 'intraday'
    assert ('In session_notes,' in text) == allowed
    assert ('Do not output session_notes' in text) != allowed
    assert 'correlation from causality' in text if profile == 'postmarket' else True


class Events:
    def __init__(self):
        self.items = []

    async def publish(self, event):
        self.items.append(event)


class ForbiddenNotes(Model):
    def __init__(self, target, persistent):
        super().__init__()
        self.target, self.persistent = target, persistent

    async def complete(self, *, role, **kwargs):
        reply = await super().complete(role=role, **kwargs)
        if role == self.target and (self.persistent or self.calls[role] == 1):
            report = json.loads(reply.content)
            report['session_notes'] = [{'stock_key': 'CN:600519', 'summary': 'Forbidden role note',
                                        'evidence_ids': ['CN:600519/technical']}]
            reply.content = json.dumps(report)
        return reply


@pytest.mark.parametrize('role', [r for r in ROLES if r != 'strategy'])
@pytest.mark.parametrize('persistent', [False, True])
def test_forbidden_notes_repair_is_bounded_and_done_requires_valid_contract(role, persistent):
    model, events = ForbiddenNotes(role, persistent), Events()
    result = asyncio.run(InvestmentResearch().run(prepared(), model=model, events=events))
    assert model.calls[role] == 2
    statuses = [e['status'] for e in events.items if e['role'] == role]
    assert statuses.count('response_received') == 2
    assert 'retrying' in statuses
    if persistent:
        assert result.completion == 'validation_failed'
        assert result.reason == 'only strategy authors session notes'
        assert 'done' not in statuses and statuses[-1] == 'failed'
        assert not any(a.role == role for a in result.artifacts)
        assert all(r.review_required and r.execution_status == 'blocked' for r in result.recommendations)
    else:
        assert result.completion == 'complete', result.reason
        assert statuses[-1] == 'done' and statuses.count('done') == 1
        assert len([a for a in result.artifacts if a.role == role]) == 1


def test_provider_failure_marks_the_role_failed_instead_of_leaving_it_running():
    class FailedRisk(Model):
        async def complete(self, *, role, **kwargs):
            if role == 'risk':
                await kwargs['before_attempt']()
                raise RuntimeError('fixture provider context unavailable')
            return await super().complete(role=role, **kwargs)
    events = Events()
    with pytest.raises(RuntimeError, match='provider context unavailable'):
        asyncio.run(InvestmentResearch().run(prepared(), model=FailedRisk(), events=events))
    assert [e['status'] for e in events.items if e['role'] == 'risk'] == ['running', 'failed']


@pytest.mark.parametrize('profile', ['premarket', 'postmarket', 'intraday'])
def test_actual_role_prompts_and_schema_agree_on_session_note_ownership(profile):
    class Check(Model):
        async def complete(self, *, role, schema, messages, **kwargs):
            allowed = role == 'strategy' and profile != 'intraday'
            assert ('session_notes' in schema['properties']) == allowed
            assert ('In session_notes,' in messages[0]['content']) == allowed
            assert ('SessionNote' in schema['$defs']) == allowed
            return await super().complete(role=role, schema=schema, messages=messages, **kwargs)
    result = asyncio.run(InvestmentResearch().run(prepared(), model=Check(), policy=ResearchPolicy(profile=profile)))
    assert result.completion == 'complete', result.reason


def test_monetary_range_guidance_distinguishes_lower_commitment_and_upper_capacity():
    events, risk = instructions('events', 'postmarket'), instructions('risk', 'postmarket')
    assert 'maximum authorization' in events and 'upper bound, never the lower bound' in events
    assert "range's lower commitment from its upper authorization" in risk
