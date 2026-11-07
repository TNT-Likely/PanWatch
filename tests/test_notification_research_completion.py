import pytest

from src.modules.notifications.service import NotificationService
from src.modules.research.orchestration.models import ResearchRun
from src.platform.persistence.models import AgentRun
from test_notifications import db, publish


@pytest.mark.parametrize(('completion', 'status', 'reason'), [
    ('validation_failed', 'review', 'only strategy authors session notes'),
    ('complete', 'review', ''),
    ('complete', 'ready', ''),
    ('budget_exhausted', 'review', 'fixture output budget'),
])
def test_notification_target_exposes_research_completion_without_rewriting_lifecycle(db, completion, status, reason):
    run = AgentRun(agent_name='premarket_outlook', status='success', result='fixture report', trace_id='fixture-trace')
    db.add(run)
    db.flush()
    db.add(ResearchRun(id='fixture-research', trace_id=run.trace_id, agent_name=run.agent_name, engine='langgraph',
        scope_hash='fixture-scope', as_of='2026-11-06T01:10:00Z', status='completed',
        payload={'completion': completion, 'status': status, 'reason': reason}))
    event = publish(db, run.id)
    db.commit()
    target = NotificationService(db).target(event.id)
    assert target['status'] == 'success'
    assert target['completion'] == completion
    assert target['result_status'] == status
    assert target['research_reason'] == reason


@pytest.mark.parametrize('trace_id', ['', 'fixture-unrelated'])
def test_legacy_notification_does_not_invent_research_completion(db, trace_id):
    run = AgentRun(agent_name='daily_report', status='success', result='legacy report', trace_id=trace_id)
    db.add(run)
    db.flush()
    # Same trace but another agent is not this notification's research result.
    db.add(ResearchRun(id='other-research', trace_id=trace_id, agent_name='premarket_outlook', engine='langgraph',
        scope_hash='other-scope', as_of='2026-11-06T01:10:00Z', status='completed',
        payload={'completion': 'validation_failed', 'status': 'review', 'reason': 'other agent'}))
    event = publish(db, run.id)
    db.commit()
    target = NotificationService(db).target(event.id)
    assert target['completion'] is target['result_status'] is target['research_reason'] is None
    assert target['result'] == 'legacy report'
