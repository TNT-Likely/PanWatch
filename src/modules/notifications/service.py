"""Transactional notification publishing and a scoped, cursor-based inbox."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from sqlalchemy import and_, case, func, or_
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from src.platform.persistence.models import (
    AgentRun, AssistantContextExport, AssistantTaskRun, ChatConversation, NotificationEvent, NotificationReceipt,
    PriceAlertHit,
)

RECIPIENT = 'installation:default'


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def now():
    return datetime.now(timezone.utc)


class NotificationService:
    def __init__(self, db: Session):
        self.db = db

    def publish(self, *, source: str, event_type: str, dedupe_key: str, subject_kind: str,
                subject_id: str, title: str, action: dict, severity='info', attention='informational',
                group_key='', correlation_id='', toast_eligible=True, occurred_at=None,
                resolved_at=None, read_at=None, event_id=None, template_params=None, expires_at=None) -> NotificationEvent:
        # SQLite's conflict-aware insert keeps the caller's transaction intact.
        # Do not use a savepoint which can commit before pysqlite starts BEGIN.
        values = dict(source=source, event_type=event_type, dedupe_key=dedupe_key,
                      subject_kind=subject_kind, subject_id=str(subject_id), severity=severity,
                      attention=attention, group_key=group_key, correlation_id=str(correlation_id),
                      template_key=event_type, template_params=template_params or {},
                      display_snapshot={'title': (title or '')[:200]}, actions=[action],
                      toast_eligible=toast_eligible, occurred_at=occurred_at or now(), resolved_at=resolved_at, expires_at=expires_at)
        if event_id is not None:
            values['id'] = event_id
        self.db.flush()
        self.db.execute(insert(NotificationEvent).values(**values).on_conflict_do_nothing(index_elements=['dedupe_key']))
        event = self.db.query(NotificationEvent).filter_by(dedupe_key=dedupe_key).one()
        self.db.execute(insert(NotificationReceipt).values(notification_id=event.id, recipient_key=RECIPIENT, read_at=read_at)
                        .on_conflict_do_nothing(index_elements=['notification_id', 'recipient_key']))
        return event

    def resolve(self, *, subject_kind: str, subject_id: str, approvals_only=False):
        query = self.db.query(NotificationEvent).filter_by(subject_kind=subject_kind, subject_id=str(subject_id))
        if approvals_only:
            query = query.filter(NotificationEvent.attention == 'action_required')
        ids = [row[0] for row in query.filter(NotificationEvent.resolved_at.is_(None)).with_entities(NotificationEvent.id)]
        if ids:
            stamp = now()
            self.db.query(NotificationEvent).filter(NotificationEvent.id.in_(ids)).update({'resolved_at': stamp}, synchronize_session='fetch')
            self.db.query(NotificationReceipt).filter(NotificationReceipt.notification_id.in_(ids), NotificationReceipt.read_at.is_(None)).update({'read_at': stamp}, synchronize_session='fetch')

    def purge_assistant_conversation(self, conversation_id: int):
        # Conversation deletion must remove even the title snapshot, including
        # notifications belonging to tasks whose run rows are already gone.
        ids = [row[0] for row in self.db.query(NotificationEvent.id).filter(
            NotificationEvent.source == 'assistant', NotificationEvent.actions[0]['conversation_id'].as_integer() == conversation_id)]
        self.db.query(NotificationReceipt).filter(NotificationReceipt.notification_id.in_(ids)).delete(synchronize_session='fetch')
        self.db.query(NotificationEvent).filter(NotificationEvent.id.in_(ids)).delete(synchronize_session='fetch')

    @staticmethod
    def pending():
        return and_(NotificationEvent.attention == 'action_required', NotificationEvent.resolved_at.is_(None),
                    or_(NotificationEvent.expires_at.is_(None), NotificationEvent.expires_at > now()))

    def query(self, source=None, view='all'):
        query = self.db.query(NotificationEvent, NotificationReceipt).join(NotificationReceipt, NotificationReceipt.notification_id == NotificationEvent.id)
        query = query.filter(NotificationReceipt.recipient_key == RECIPIENT)
        if source:
            query = query.filter(NotificationEvent.source == source)
        query = query.filter(NotificationReceipt.archived_at.isnot(None) if view == 'archived' else NotificationReceipt.archived_at.is_(None))
        if view == 'unread':
            query = query.filter(NotificationReceipt.read_at.is_(None), NotificationEvent.resolved_at.is_(None),
                                 or_(NotificationEvent.expires_at.is_(None), NotificationEvent.expires_at > now()))
        if view == 'pending':
            query = query.filter(self.pending())
        if view == 'attention':
            query = query.filter(NotificationEvent.resolved_at.is_(None),
                or_(NotificationEvent.expires_at.is_(None), NotificationEvent.expires_at > now()),
                or_(NotificationReceipt.read_at.is_(None), self.pending()))
        return query

    def summary(self):
        query = self.query()
        return {
            'unread_count': self.query(view='unread').count(),
            'pending_action_count': query.filter(self.pending()).count(),
            'observed_id': query.with_entities(func.max(NotificationEvent.id)).scalar() or 0,
        }

    def available_ids(self, rows):
        available = set()
        for kind, model in [('assistant_task', AssistantTaskRun), ('assistant_export', AssistantContextExport), ('agent_run', AgentRun), ('alert_hit', PriceAlertHit)]:
            ids = [int(e.subject_id) for e, _ in rows if e.subject_kind == kind]
            existing = {str(row[0]) for row in self.db.query(model.id).filter(model.id.in_(ids))} if ids else set()
            available.update(e.id for e, _ in rows if e.subject_kind == kind and e.subject_id in existing)
        # A task without its conversation is not a valid navigation target.
        conversation_ids = [a.get('conversation_id') for e, _ in rows for a in e.actions if e.source == 'assistant']
        conversations = {row[0] for row in self.db.query(ChatConversation.id).filter(ChatConversation.id.in_(conversation_ids))} if conversation_ids else set()
        return {e.id for e, _ in rows if e.id in available and (e.source != 'assistant' or any(a.get('conversation_id') in conversations for a in e.actions))}

    def serialize(self, rows):
        available = self.available_ids(rows)
        conversation_ids = [action.get('conversation_id') for event, _ in rows if event.source == 'assistant' for action in event.actions]
        titles = dict(self.db.query(ChatConversation.id, ChatConversation.title).filter(ChatConversation.id.in_(conversation_ids)).all()) if conversation_ids else {}
        stamp = now()
        return [dict(id=e.id, source=e.source, event_type=e.event_type, severity=e.severity, attention=e.attention,
                     title=(titles.get(e.actions[0].get('conversation_id')) if e.source == 'assistant' and e.actions else None) or e.display_snapshot.get('title', ''), template_key=e.template_key, template_params=e.template_params,
                     group_key=e.group_key, toast_eligible=e.toast_eligible, occurred_at=utc(e.occurred_at),
                     resolved_at=utc(e.resolved_at), expires_at=utc(e.expires_at), read_at=utc(r.read_at), archived_at=utc(r.archived_at),
                     action_required=e.attention == 'action_required' and e.resolved_at is None and (e.expires_at is None or utc(e.expires_at) > stamp),
                     available=e.id in available, actions=e.actions if e.id in available else []) for e, r in rows]

    def list(self, *, source=None, view='all', cursor=None, limit=30):
        scope = [source, view]
        if cursor:
            try:
                data = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                assert data['scope'] == scope
                upper, rank_value, last = int(data['upper']), int(data['rank']), int(data['last'])
                snapshot = datetime.fromisoformat(data['at'])
                assert upper >= last > 0 and rank_value in ((0, 1, 2) if view == 'attention' else (0, 1)) and snapshot.tzinfo is not None
            except (ValueError, KeyError, TypeError, OverflowError, AssertionError) as exc:
                raise ValueError('Invalid notification cursor') from exc
        else:
            upper = self.query(source, view).with_entities(func.max(NotificationEvent.id)).scalar() or 0
            snapshot = now()
            rank_value, last = None, None
        # Freeze unread/read ordering at the first page's time. Reading a row
        # between pages must not move it to another rank and duplicate it.
        rank = case((or_(NotificationReceipt.read_at.is_(None), NotificationReceipt.read_at > snapshot), 0), else_=1)
        if view == 'attention':
            rank = case((self.pending(), 0), (or_(NotificationReceipt.read_at.is_(None), NotificationReceipt.read_at > snapshot), 1), else_=2)
        query = self.query(source, view).filter(NotificationEvent.id <= upper)
        if cursor:
            query = query.filter(or_(rank > rank_value, and_(rank == rank_value, NotificationEvent.id < last)))
        page = query.add_columns(rank.label('rank')).order_by(rank, NotificationEvent.id.desc()).limit(limit + 1).all()
        has_more = len(page) > limit
        page = page[:limit]
        next_cursor = None
        if has_more:
            tail = page[-1]
            data = dict(scope=scope, upper=upper, at=snapshot.isoformat(), rank=tail[2], last=tail[0].id)
            next_cursor = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')
        return dict(items=self.serialize([(e, r) for e, r, _ in page]), next_cursor=next_cursor, observed_id=upper)

    def select(self, command):
        query = self.query(command.source, command.view)
        return query.filter(NotificationEvent.id <= command.through_id) if command.through_id else query.filter(NotificationEvent.id.in_(command.ids))

    def mark_read(self, command):
        ids = [e.id for e, _ in self.select(command).filter(NotificationReceipt.read_at.is_(None))]
        updated = self.db.query(NotificationReceipt).filter(NotificationReceipt.recipient_key == RECIPIENT, NotificationReceipt.notification_id.in_(ids)).update({'read_at': now()}, synchronize_session='fetch')
        self.db.commit()
        return updated

    def archive(self, command):
        query = self.select(command).filter(NotificationReceipt.archived_at.is_(None) if command.archived else NotificationReceipt.archived_at.isnot(None))
        if command.archived and query.filter(self.pending()).count():
            raise ValueError('Pending approvals cannot be archived')
        ids = [e.id for e, _ in query]
        updated = self.db.query(NotificationReceipt).filter(NotificationReceipt.recipient_key == RECIPIENT, NotificationReceipt.notification_id.in_(ids)).update({'archived_at': now() if command.archived else None}, synchronize_session='fetch')
        self.db.commit()
        return updated

    def target(self, notification_id):
        row = self.db.query(NotificationEvent, NotificationReceipt).join(NotificationReceipt).filter(NotificationEvent.id == notification_id, NotificationReceipt.recipient_key == RECIPIENT).first()
        if row is None or notification_id not in self.available_ids([row]):
            raise LookupError('Notification source is unavailable')
        event, _ = row
        if event.subject_kind == 'assistant_task':
            return dict(kind='assistant_conversation', conversation_id=event.actions[0]['conversation_id'])
        if event.subject_kind == 'assistant_export':
            job = self.db.get(AssistantContextExport, int(event.subject_id))
            return dict(kind='assistant_export', export_id=job.id, conversation_id=job.conversation_id)
        if event.subject_kind == 'agent_run':
            run = self.db.get(AgentRun, int(event.subject_id))
            return dict(kind='agent_run', id=run.id, agent_name=run.agent_name, status=run.status, result=run.result,
                        error=run.error, occurred_at=utc(run.created_at), notify_attempted=run.notify_attempted, notify_sent=run.notify_sent)
        hit = self.db.get(PriceAlertHit, int(event.subject_id))
        return dict(kind='price_alert_hit', id=hit.id, rule_id=hit.rule_id, name=hit.stock.name if hit.stock else '',
                    symbol=hit.stock.symbol if hit.stock else '', snapshot=hit.trigger_snapshot, occurred_at=utc(hit.trigger_time),
                    notify_success=hit.notify_success)
