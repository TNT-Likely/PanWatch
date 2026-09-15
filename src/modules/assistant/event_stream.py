"""Database-backed SSE replay for durable assistant task events."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from src.platform.events.sse import format_sse_comment, format_sse_event
from src.platform.persistence.database import SessionLocal
from src.platform.tasking.contracts import TaskEventType, TaskStatus

from .repository import AssistantRepository

_SSE_EVENT_NAMES = {
    TaskEventType.TASK_CREATED: "task_created",
    TaskEventType.TASK_QUEUED: "task_queued",
    TaskEventType.TASK_STARTED: "run_started",
    TaskEventType.CONTEXT_PREPARED: "context_prepared",
    TaskEventType.STEP_STARTED: "step_started",
    TaskEventType.STEP_PROGRESS: "step_updated",
    TaskEventType.EXTENSION_EVENT: "extension_event",
    TaskEventType.ANSWER_TOKEN: "token",
    TaskEventType.TOOL_STARTED: "tool_call_start",
    TaskEventType.TOOL_COMPLETED: "tool_result",
    TaskEventType.CHECKPOINT_SAVED: "checkpoint_saved",
    TaskEventType.APPROVAL_REQUIRED: "approval_required",
    TaskEventType.TASK_PAUSED: "paused",
    TaskEventType.TASK_RETRY_SCHEDULED: "retry_scheduled",
    TaskEventType.TASK_COMPLETED: "done",
    TaskEventType.TASK_FAILED: "error",
    TaskEventType.TASK_CANCELLED: "cancelled",
}


def _load_task_events(
    task_id: int,
    after_sequence: int,
    session_factory: Callable[[], Session],
):
    db = session_factory()
    try:
        repository = AssistantRepository(db)
        events = repository.list_task_events(task_id, after_sequence=after_sequence)
        snapshot = repository.get_task_snapshot(task_id)
        return events, snapshot
    finally:
        db.close()


async def subscribe_task_events(
    task_id: int,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    after_sequence: int = 0,
    heartbeat_sec: float = 15.0,
    poll_sec: float = 0.25,
):
    """Replay persisted events, then tail until terminal or resumable wait state."""
    cursor = max(0, int(after_sequence))
    last_activity = time.monotonic()
    while True:
        events, snapshot = await asyncio.to_thread(
            _load_task_events, task_id, cursor, session_factory
        )
        if events:
            for event in events:
                cursor = event.sequence
                event_type = _SSE_EVENT_NAMES.get(TaskEventType(event.event_type), event.event_type)
                yield format_sse_event(event.sequence, event_type, event.data or {})
            last_activity = time.monotonic()
            continue

        if TaskStatus(snapshot["status"]) in {
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.WAITING_RETRY,
            TaskStatus.WAITING_CALLBACK,
        } or TaskStatus(snapshot["status"]).is_terminal:
            return
        if time.monotonic() - last_activity >= heartbeat_sec:
            last_activity = time.monotonic()
            yield format_sse_comment()
        await asyncio.sleep(poll_sec)
