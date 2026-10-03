"""Global inbox semantics, legacy migration, and actual source integrations."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import (AgentRun, AssistantTaskNotification, ChatConversation,
    NotificationEvent, NotificationReceipt, PriceAlertRule, PriceAlertHit, Stock)
from src.modules.notifications.service import NotificationService
from src.modules.notifications.schemas import NotificationSelection, ArchiveNotifications
from src.modules.notifications.sources import agent_result, price_hit
from src.modules.assistant.repository import AssistantRepository
from src.platform.tasking.contracts import TaskEventType, TaskStatus

@pytest.fixture
def db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def publish(db, number, source='agent', pending=False):
    return NotificationService(db).publish(source=source, event_type='agent_completed', dedupe_key=f'test:{number}',
        subject_kind='agent_run', subject_id=str(number), title=f'Report {number}', action=dict(kind='agent_run', run_id=number),
        attention='action_required' if pending else 'informational')


def test_transactional_publish_duplicate_and_rollback(db):
    first = publish(db, 1)
    assert publish(db, 1).id == first.id
    assert db.query(NotificationEvent).count() == db.query(NotificationReceipt).count() == 1
    db.rollback()
    assert db.query(NotificationEvent).count() == db.query(NotificationReceipt).count() == 0


def test_read_and_pending_are_independent_and_archive_requires_resolution(db):
    event = publish(db, 1, pending=True); db.commit()
    service = NotificationService(db)
    assert service.mark_read(NotificationSelection(ids=[event.id])) == 1
    assert service.summary()['pending_action_count'] == 1
    assert service.summary()['unread_count'] == 0
    assert service.list(view='pending')['items'][0]['action_required'] is True
    with pytest.raises(ValueError, match='cannot be archived'):
        service.archive(ArchiveNotifications(ids=[event.id]))
    service.resolve(subject_kind='agent_run', subject_id='1'); db.commit()
    assert service.summary()['pending_action_count'] == 0
    assert service.archive(ArchiveNotifications(ids=[event.id])) == 1
    assert service.list()['items'] == []
    assert service.list(view='archived')['items'][0]['id'] == event.id
    service.archive(ArchiveNotifications(ids=[event.id], view='archived', archived=False))
    assert len(service.list()['items']) == 1


def test_read_boundary_filters_and_recipient_scope(db):
    old = publish(db, 1, source='assistant'); db.commit()
    cursor = NotificationService(db).summary()['observed_id']
    other = publish(db, 2); later = publish(db, 3, source='assistant'); db.commit()
    service = NotificationService(db)
    assert service.mark_read(NotificationSelection(through_id=cursor, source='assistant')) == 1
    assert service.summary()['unread_count'] == 2
    assert service.mark_read(NotificationSelection(ids=[old.id])) == 0
    receipt = db.query(NotificationReceipt).filter_by(notification_id=other.id).one()
    receipt.recipient_key = 'installation:other'; db.commit()
    assert service.mark_read(NotificationSelection(ids=[other.id])) == 0
    assert service.summary()['unread_count'] == 1
    assert service.list()['items'][0]['id'] == later.id
    with pytest.raises(LookupError): service.target(other.id)


def test_attention_preview_keeps_read_approvals_hides_read_history_and_preserves_new_arrivals(db):
    pending = publish(db, 1, pending=True)
    history = publish(db, 2)
    unread = publish(db, 3)
    expired = publish(db, 4, pending=True)
    expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    service = NotificationService(db)
    service.mark_read(NotificationSelection(ids=[pending.id, history.id]))
    page = service.list(view='attention', limit=1)
    assert [item['id'] for item in page['items']] == [pending.id]
    second = service.list(view='attention', cursor=page['next_cursor'], limit=1)
    assert [item['id'] for item in second['items']] == [unread.id]
    later = publish(db, 5); db.commit()
    service.mark_read(NotificationSelection(through_id=page['observed_id'], view='attention'))
    assert [item['id'] for item in service.list(view='attention')['items']] == [pending.id, later.id]
    assert service.summary()['pending_action_count'] == 1


def test_pagination_freezes_upper_bound_and_unread_rank_during_reads(db):
    items = [publish(db, i) for i in range(1, 9)]; db.commit()
    service = NotificationService(db)
    service.mark_read(NotificationSelection(ids=[items[1].id, items[3].id]))
    first = service.list(limit=3)
    service.mark_read(NotificationSelection(ids=[first['items'][0]['id'], items[0].id]))
    new = publish(db, 9); db.commit()
    found = [item['id'] for item in first['items']]
    cursor = first['next_cursor']
    while cursor:
        page = service.list(limit=3, cursor=cursor)
        found.extend(item['id'] for item in page['items']); cursor = page['next_cursor']
    assert len(found) == len(set(found)) == 8
    assert set(found) == {item.id for item in items} and new.id not in found
    with pytest.raises(ValueError): service.list(cursor=first['next_cursor'], source='assistant')
    with pytest.raises(ValueError): service.list(cursor='invalid')


def test_expired_approval_does_not_count_as_pending_or_unread(db):
    event = publish(db, 1, pending=True)
    event.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1); db.commit()
    service = NotificationService(db)
    assert service.summary()['pending_action_count'] == service.summary()['unread_count'] == 0
    assert service.list()['items'][0]['action_required'] is False


def test_m130_preserves_legacy_ids_timestamps_read_resolved_and_suppresses_toasts(db):
    from src.platform.persistence.migrations import _m130_global_notifications
    repository = AssistantRepository(db)
    conversation = repository.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
    conversation.title = 'Legacy research'
    task = repository.create_task(conversation_id=conversation.id, user_message_id=None, context={})
    stamp = datetime(2026, 9, 28, 1, 2, 3)
    db.add_all([AssistantTaskNotification(id=50, task_run_id=task.id, event_sequence=20, kind='completed', read_at=stamp, created_at=stamp),
                AssistantTaskNotification(id=51, task_run_id=task.id, event_sequence=21, kind='awaiting_approval', resolved_at=stamp, created_at=stamp)])
    db.commit()
    with db.bind.begin() as connection:
        _m130_global_notifications(connection); _m130_global_notifications(connection)
    db.expire_all()
    events = db.query(NotificationEvent).order_by(NotificationEvent.id).all()
    assert [e.id for e in events] == [50, 51]
    assert all(not e.toast_eligible for e in events)
    assert events[1].resolved_at == events[0].occurred_at == stamp
    receipts = db.query(NotificationReceipt).order_by(NotificationReceipt.notification_id).all()
    assert receipts[0].read_at == stamp and receipts[1].read_at is None
    assert NotificationService(db).summary()['unread_count'] == 0
    assert publish(db, 99).id > 51


def test_assistant_uses_global_inbox_and_deletion_removes_title_snapshot(db):
    repository = AssistantRepository(db)
    conversation = repository.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
    task = repository.create_task(conversation_id=conversation.id, user_message_id=None, context={})
    repository.append_task_event(task.id, TaskEventType.TASK_PAUSED, status=TaskStatus.WAITING_APPROVAL)
    service = NotificationService(db)
    event = service.list()['items'][0]
    assert event['source'] == 'assistant' and event['available']
    assert service.target(event['id']) == dict(kind='assistant_conversation', conversation_id=conversation.id)
    repository.read_notifications(ids=[event['id']])
    assert service.summary()['pending_action_count'] == 1
    repository.cancel_task(task.id)
    assert service.summary()['pending_action_count'] == 0
    assert db.query(AssistantTaskNotification).count() == 0
    repository.delete_conversation(conversation)
    assert service.list()['items'] == [] and db.query(NotificationReceipt).count() == 0


def test_agent_result_idempotent_and_failure_episode_toasts(db, monkeypatch):
    from src.modules.automation import agent_runs
    monkeypatch.setattr(agent_runs, 'SessionLocal', sessionmaker(bind=db.bind))
    agent_runs.record_agent_run('daily_report', 'success', result='Report result', trace_id='test-run', notify_attempted=True, notify_sent=False)
    agent_runs.record_agent_run('daily_report', 'success', result='duplicate', trace_id='test-run')
    db.expire_all()
    assert db.query(AgentRun).count() == db.query(NotificationEvent).count() == 1
    notice = NotificationService(db).list()['items'][0]
    assert not notice['toast_eligible']
    target = NotificationService(db).target(notice['id'])
    assert target['status'] == 'success' and target['notify_sent'] is False and target['result'] == 'Report result'
    agent_runs.record_agent_run('daily_report', 'failed', trace_id='failed-1')
    agent_runs.record_agent_run('daily_report', 'failed', trace_id='failed-2')
    failed = db.query(NotificationEvent).filter_by(event_type='agent_failed').order_by(NotificationEvent.id).all()
    assert [item.toast_eligible for item in failed] == [True, False]
    agent_runs.record_agent_run('daily_report', 'success', trace_id='recovered')
    db.expire_all(); assert all(row.resolved_at is not None for row in failed)
    agent_runs.record_agent_run('daily_report', 'failed', trace_id='failed-3')
    assert db.query(NotificationEvent).order_by(NotificationEvent.id.desc()).first().toast_eligible is True


def test_price_hit_survives_delivery_failure_and_repeated_scan_deduplicates(db, monkeypatch):
    import asyncio
    from src.modules.market import price_alert_engine
    stock = Stock(symbol='601238', name='Example', market='CN'); db.add(stock); db.flush()
    rule = PriceAlertRule(stock_id=stock.id, name='Trigger', market_hours_mode='always', cooldown_minutes=0,
                          max_triggers_per_day=0, condition_group={'op':'and','items':[{'type':'price','op':'>=','value':5}]})
    db.add(rule); db.commit()
    monkeypatch.setattr(price_alert_engine, 'SessionLocal', sessionmaker(bind=db.bind))
    engine = price_alert_engine.PriceAlertEngine()
    monkeypatch.setattr('src.platform.scheduling.trading_calendar.is_trading_day', lambda *args: True)
    monkeypatch.setattr(engine, '_fetch_quotes_map', AsyncMock(return_value={('CN','601238'):{'current_price':6}}))
    async def delivery(session, *args):
        # The same transaction already committed both records before I/O.
        with sessionmaker(bind=db.bind)() as fresh:
            assert fresh.query(PriceAlertHit).count() == fresh.query(NotificationEvent).count() == 1
        return False, 'channel failure'
    monkeypatch.setattr(engine, '_send_notify', delivery)
    assert asyncio.run(engine.scan_once())['triggered'] == 1
    assert asyncio.run(engine.scan_once())['triggered'] == 0
    db.expire_all()
    assert db.query(PriceAlertHit).count() == db.query(NotificationEvent).count() == 1
    notice = NotificationService(db).list()['items'][0]
    assert notice['source'] == 'market' and notice['available']
    assert NotificationService(db).target(notice['id'])['notify_success'] is False
    from src.modules.market.price_alert_service import delete_alert_rule
    delete_alert_rule(db, rule.id)
    assert NotificationService(db).list()['items'][0]['available'] is False
    with pytest.raises(LookupError): NotificationService(db).target(notice['id'])


def test_three_sources_share_one_inbox(db):
    repository = AssistantRepository(db)
    conversation = repository.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
    task = repository.create_task(conversation_id=conversation.id, user_message_id=None, context={})
    repository.finish_task(task.id, status='failed', final_message_id=None)
    run = AgentRun(agent_name='daily_report', status='success', trace_id='report'); db.add(run); db.flush(); agent_result(db, run)
    stock = Stock(symbol='X', name='Example', market='US'); db.add(stock); db.flush()
    rule = PriceAlertRule(stock_id=stock.id); db.add(rule); db.flush()
    hit = PriceAlertHit(rule_id=rule.id, stock_id=stock.id, trigger_bucket='test', trigger_snapshot={}); db.add(hit); db.flush(); price_hit(db, hit, rule)
    db.commit()
    service = NotificationService(db)
    assert {item['source'] for item in service.list()['items']} == {'assistant','market','agent'}
    assert all(len(service.list(source=source)['items']) == 1 for source in ['assistant','market','agent'])


def test_http_validation_and_authentication_surface(db):
    from src.modules.notifications.api import router
    app = FastAPI(); app.include_router(router, prefix='/notifications')
    def database(): yield db
    app.dependency_overrides[get_db] = database
    client = TestClient(app)
    event = publish(db, 1); db.commit()
    assert client.get('/notifications/summary').json()['unread_count'] == 1
    assert client.get('/notifications?source=system').status_code == 422
    assert client.get('/notifications?cursor=invalid').status_code == 400
    assert client.post('/notifications/read', json={}).status_code == 422
    assert client.post('/notifications/read', json={'ids':[event.id], 'through_id':event.id}).status_code == 422
    assert client.post('/notifications/read', json={'ids':[event.id]}).json() == {'updated':1}
    from src.bootstrap.application import app as production
    from src.modules.administration.api.auth import get_current_user
    from fastapi import HTTPException
    def deny(): raise HTTPException(status_code=401)
    assert '/api/notifications/summary' in production.openapi()['paths']
    production.dependency_overrides[get_current_user] = deny
    production.dependency_overrides[get_db] = database
    try:
        assert TestClient(production).get('/api/notifications/summary').status_code == 401
        assert TestClient(production).post('/api/notifications/read', json={'ids':[event.id]}).status_code == 401
    finally:
        production.dependency_overrides.pop(get_current_user, None)
        production.dependency_overrides.pop(get_db, None)

@pytest.mark.parametrize('mode', ['batch','single'])
def test_scheduler_delivery_failure_does_not_mark_generated_report_failed(monkeypatch, mode):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.modules.automation import agent_scheduler
    from src.platform.marketdata.models import MarketCode
    result = SimpleNamespace(content='Complete report ' * 300, raw_data={'notify_error':'delivery failed', 'notified':False})
    agent = SimpleNamespace(display_name='Report', run=AsyncMock(return_value=result), run_single=AsyncMock(return_value=result))
    context = SimpleNamespace(model_label='test', watchlist=[SimpleNamespace(symbol='EXAMPLE', market=MarketCode.US)])
    scheduler = agent_scheduler.AgentScheduler()
    scheduler.agents['daily_report'] = agent
    scheduler.execution_modes['daily_report'] = mode
    scheduler.set_context_builder(lambda name: context)
    monkeypatch.setattr(agent_scheduler, 'market_allowed', lambda *args: True)
    record = Mock(); monkeypatch.setattr(agent_scheduler, 'record_agent_run', record)
    asyncio.run(scheduler._run_agent('daily_report'))
    values=record.call_args.kwargs
    assert values['status'] == 'success' and values['error'] == ''
    assert values['notify_attempted'] is True and values['notify_sent'] is False
    if mode == 'batch': assert values['result'] == result.content


def test_assistant_notification_inherits_real_approval_expiry(db):
    from src.platform.persistence.models import AssistantToolApproval
    repository = AssistantRepository(db)
    conversation = repository.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
    task = repository.create_task(conversation_id=conversation.id, user_message_id=None, context={})
    expiry = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(AssistantToolApproval(id='expiry-fixture', task_run_id=task.id, call_id='expiry-call', tool_name='create_price_alert',
                                risk='write', status='pending', expires_at=expiry, arguments={}))
    db.commit()
    repository.append_task_event(task.id, TaskEventType.TASK_PAUSED, status=TaskStatus.WAITING_APPROVAL)
    notice = NotificationService(db).list()['items'][0]
    assert notice['expires_at'] == expiry
    assert not notice['action_required']
    assert NotificationService(db).summary()['pending_action_count'] == 0


def test_waiting_for_retry_does_not_create_a_false_approval_notification(db):
    repository = AssistantRepository(db)
    conversation = repository.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
    task = repository.create_task(conversation_id=conversation.id, user_message_id=None, context={})
    repository.append_task_event(task.id, TaskEventType.TASK_PAUSED, status=TaskStatus.WAITING_RETRY)
    assert NotificationService(db).summary()['pending_action_count'] == 0
    assert NotificationService(db).list()['items'] == []


def test_source_failure_rolls_back_agent_result_and_inbox_together(db, monkeypatch):
    from src.modules.automation import agent_runs
    from src.modules.notifications import sources
    monkeypatch.setattr(agent_runs, 'SessionLocal', sessionmaker(bind=db.bind))
    def fail(*args): raise RuntimeError('notification write failed')
    monkeypatch.setattr(sources, 'agent_result', fail)
    agent_runs.record_agent_run('daily_report', 'success', result='Uncommitted report', trace_id='rollback-fixture')
    assert db.query(AgentRun).count() == db.query(NotificationEvent).count() == 0


def test_idle_intraday_record_does_not_publish_or_resolve_previous_failure(db, monkeypatch):
    from src.modules.automation import agent_runs

    monkeypatch.setattr(agent_runs, 'SessionLocal', sessionmaker(bind=db.bind))
    agent_runs.record_agent_run('intraday_monitor', 'failed', trace_id='failed-intraday')
    agent_runs.record_agent_run(
        'intraday_monitor', 'success', trace_id='idle-intraday', trigger_source='schedule',
        result='single mode executed 0, skipped 1, total 1',
    )
    db.expire_all()
    assert db.query(AgentRun).count() == 2
    assert db.query(NotificationEvent).count() == 1
    failure = db.query(NotificationEvent).one()
    assert failure.resolved_at is None
    assert NotificationService(db).summary()['unread_count'] == 1
    agent_runs.record_agent_run(
        'intraday_monitor', 'success', trace_id='real-intraday', trigger_source='schedule',
        result='single mode executed 1, skipped 0, total 1',
    )
    db.expire_all()
    assert failure.resolved_at is not None
    assert db.query(NotificationEvent).count() == 2


def test_idle_intraday_migration_archives_only_proven_noise_and_retains_history(db, monkeypatch):
    from src.platform.persistence import migrations

    stamp = datetime(2026, 9, 30, tzinfo=timezone.utc)
    runs = [AgentRun(agent_name='intraday_monitor', status='success', trigger_source='schedule',
                    result='single mode executed 0, skipped 1, total 1') for _ in range(66)]
    # Similar-looking reports, manual results and failures must remain visible.
    retained = [
        dict(result='single mode executed 1, skipped 0, total 1'),
        dict(result='single mode executed 0, skipped 0, total 0', status='failed'),
        dict(result='single mode executed 0, skipped 0, total 0', trigger_source='manual'),
        dict(result='single mode executed 0, skipped 0, total 0', agent_name='daily_report'),
        dict(result='single mode executed 0, skipped 1, total 2'),
        dict(result='single mode executed 0, skipped 1, total 1\nReal report'),
        dict(result='single mode executed 0, skipped 0, total 0', error='Analysis failed'),
        dict(result='single mode executed 0, skipped 0, total 0', notify_attempted=True),
        dict(result='single mode executed 0, skipped 0, total 0', notify_sent=True),
    ]
    for values in retained:
        fields = dict(agent_name='intraday_monitor', status='success', trigger_source='schedule')
        fields.update(values)
        runs.append(AgentRun(**fields))
    # Empty watchlist also generated idle notices in the old scheduler.
    runs.append(AgentRun(agent_name='intraday_monitor', status='success', trigger_source='schedule',
                         result='single mode executed 0, skipped 0, total 0'))
    db.add_all(runs); db.flush()
    for run in runs:
        NotificationService(db).publish(
            source='agent', event_type='agent_failed' if run.status == 'failed' else 'agent_completed',
            subject_kind='agent_run', subject_id=str(run.id), dedupe_key=f'legacy:{run.id}',
            title=run.agent_name, action=dict(kind='agent_run', run_id=run.id),
        )
    receipt = db.query(NotificationReceipt).filter_by(notification_id=1).one()
    receipt.read_at = stamp; receipt.archived_at = stamp
    # Migration must clean all recipients, not just the current installation.
    db.add(NotificationReceipt(notification_id=2, recipient_key='installation:other'))
    db.commit()
    migration = next(m for m in migrations.MIGRATIONS if m.version == 133)
    monkeypatch.setattr(migrations, 'MIGRATIONS', (migration,))
    migrations.run_versioned_migrations(db.bind)
    assert not migrations.has_pending_migrations(db.bind)
    migrations.run_versioned_migrations(db.bind)
    db.expire_all()
    service = NotificationService(db)
    assert len(service.list(limit=100)['items']) == len(retained)
    assert service.summary()['unread_count'] == len(retained)
    assert len(service.list(view='archived', limit=100)['items']) == 67
    assert db.query(AgentRun).count() == db.query(NotificationEvent).count() == 76
    assert receipt.read_at == receipt.archived_at == stamp.replace(tzinfo=None)
    other = db.query(NotificationReceipt).filter_by(recipient_key='installation:other').one()
    assert other.read_at is not None and other.archived_at is not None
