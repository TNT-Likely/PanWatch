"""Database-backed SSE replay for durable assistant task events."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from src.platform.events.sse import format_sse_comment, format_sse_event
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AssistantTaskEvent, AssistantTaskRun
from src.platform.tasking.contracts import TaskEventType, TaskStatus

from .trace import SSE_EVENT_NAMES


def _load_task_events(
    task_id: int,
    after_sequence: int,
    session_factory: Callable[[], Session],
):
    db = session_factory()
    try:
        # Tailing needs only the status. A full snapshot also reloads trace,
        # approvals, usage and tool history on every poll.
        status = db.query(AssistantTaskRun.status).filter(AssistantTaskRun.id == task_id).scalar()
        if status is None:
            raise LookupError("助手任务不存在")
        events = (
            db.query(AssistantTaskEvent)
            .filter(AssistantTaskEvent.task_run_id == task_id, AssistantTaskEvent.sequence > after_sequence)
            .order_by(AssistantTaskEvent.sequence.asc())
            .all()
        )
        return events, TaskStatus(status)
    finally:
        db.close()


async def subscribe_task_events(
    task_id: int,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    after_sequence: int = 0,
    heartbeat_sec: float = 15.0,
    poll_sec: float = 0.1,
):
    """Replay persisted events, then tail until terminal or resumable wait state."""
    cursor = max(0, int(after_sequence))
    last_activity = time.monotonic()
    while True:
        events, status = await asyncio.to_thread(
            _load_task_events, task_id, cursor, session_factory
        )
        if events:
            for event in events:
                cursor = event.sequence
                event_type = SSE_EVENT_NAMES.get(TaskEventType(event.event_type), event.event_type)
                yield format_sse_event(event.sequence, event_type, event.data or {})
            last_activity = time.monotonic()
            continue

        if status in {
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.WAITING_RETRY,
            TaskStatus.WAITING_CALLBACK,
        } or status.is_terminal:
            return
        if time.monotonic() - last_activity >= heartbeat_sec:
            last_activity = time.monotonic()
            yield format_sse_comment()
        await asyncio.sleep(poll_sec)
