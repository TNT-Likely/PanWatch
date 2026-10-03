"""Business-owned adapters; all writes share the caller's transaction."""
from sqlalchemy import func
from src.platform.scheduling.run_summary import is_idle_single_summary
from src.platform.persistence.models import AssistantToolApproval, ChatConversation, NotificationEvent
from .service import NotificationService
from .presentation import agent_notification_params


def assistant_event(db, task, sequence, kind, *, occurred_at=None, read_at=None, resolved_at=None, legacy_id=None):
    conversation = db.get(ChatConversation, task.conversation_id)
    expires_at = db.query(func.min(AssistantToolApproval.expires_at)).filter(
        AssistantToolApproval.task_run_id == task.id, AssistantToolApproval.status == 'pending').scalar() if kind == 'awaiting_approval' else None
    return NotificationService(db).publish(source='assistant', event_type=f'assistant_{kind}',
        dedupe_key=f'assistant:{task.id}:{sequence}', subject_kind='assistant_task', subject_id=str(task.id),
        correlation_id=str(task.id), title=conversation.title if conversation else '',
        severity='warning' if kind == 'failed' else 'info', attention='action_required' if kind == 'awaiting_approval' else 'informational',
        group_key=f'assistant:{task.id}', action=dict(kind='assistant_conversation', conversation_id=task.conversation_id, task_id=task.id),
        occurred_at=occurred_at, read_at=read_at, resolved_at=resolved_at, toast_eligible=legacy_id is None, event_id=legacy_id, expires_at=expires_at)


def agent_result(db, run):
    if (run.agent_name == 'intraday_monitor' and run.status == 'success'
            and run.trigger_source == 'schedule' and not run.error
            and not run.notify_attempted and not run.notify_sent
            and is_idle_single_summary(run.result)):
        return None
    service = NotificationService(db)
    group = f'agent:{run.agent_name}:failures'
    episode = db.query(NotificationEvent).filter(NotificationEvent.source == 'agent', NotificationEvent.event_type == 'agent_failed', NotificationEvent.group_key.like(group + ':%'), NotificationEvent.resolved_at.is_(None))
    previous = episode.order_by(NotificationEvent.id).first()
    if run.status == 'success':
        # Resolve the failure episode without losing its historical records.
        ids = [r.subject_id for r in episode]
        for subject_id in ids:
            service.resolve(subject_kind='agent_run', subject_id=subject_id)
    return service.publish(source='agent', event_type='agent_failed' if run.status == 'failed' else 'agent_completed',
        dedupe_key=f'agent:{run.trace_id or run.id}:{run.status}', subject_kind='agent_run', subject_id=str(run.id),
        correlation_id=run.trace_id or str(run.id), title=run.agent_name, group_key=(previous.group_key if previous else f'{group}:{run.trace_id or run.id}') if run.status == 'failed' else f'agent:{run.agent_name}',
        severity='warning' if run.status == 'failed' else 'info', action=dict(kind='agent_run', run_id=run.id),
        template_params=agent_notification_params(db, [run])[run.id],
        toast_eligible=run.status == 'failed' and previous is None)


def price_hit(db, hit, rule):
    stock = rule.stock
    return NotificationService(db).publish(source='market', event_type='price_alert_hit', dedupe_key=f'price_alert:{hit.id}',
        subject_kind='alert_hit', subject_id=str(hit.id), title=rule.name or stock.name or stock.symbol,
        correlation_id=str(hit.id), group_key=f'price_alert:{rule.id}', severity='warning',
        action=dict(kind='price_alert_hit', hit_id=hit.id, rule_id=rule.id), occurred_at=hit.trigger_time,
        template_params=dict(symbol=stock.symbol, market=stock.market, price=(hit.trigger_snapshot.get('quote') or {}).get('current_price')))
