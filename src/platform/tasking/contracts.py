"""Provider-neutral task state and event contracts.

These contracts describe the host task lifecycle around an Agent runtime.
They deliberately do not know about queues, databases, FastAPI, or SSE.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    """Durable host-owned state for a long-running Agent task."""

    PENDING = "pending"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    WAITING_APPROVAL = "awaiting_approval"
    WAITING_RETRY = "waiting_retry"
    WAITING_CALLBACK = "waiting_callback"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    DEAD_LETTER = "dead_letter"

    @property
    def is_terminal(self) -> bool:
        return self in {
            self.COMPLETED,
            self.FAILED,
            self.CANCELLED,
            self.EXPIRED,
            self.DEAD_LETTER,
        }


class TaskEventType(StrEnum):
    """Facts that can be persisted and replayed to task subscribers."""

    TASK_CREATED = "task_created"
    TASK_QUEUED = "task_queued"
    TASK_STARTED = "task_started"
    STEP_STARTED = "step_started"
    STEP_PROGRESS = "step_progress"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    CHECKPOINT_SAVED = "checkpoint_saved"
    APPROVAL_REQUIRED = "approval_required"
    TASK_PAUSED = "task_paused"
    TASK_RETRY_SCHEDULED = "task_retry_scheduled"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"


_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.FAILED}),
    TaskStatus.QUEUED: frozenset({TaskStatus.DISPATCHED, TaskStatus.CANCELLED, TaskStatus.EXPIRED}),
    TaskStatus.DISPATCHED: frozenset({TaskStatus.RUNNING, TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.WAITING_RETRY,
            TaskStatus.WAITING_CALLBACK,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.EXPIRED,
            TaskStatus.DEAD_LETTER,
        }
    ),
    TaskStatus.WAITING_APPROVAL: frozenset(
        {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.EXPIRED, TaskStatus.FAILED}
    ),
    TaskStatus.WAITING_RETRY: frozenset(
        {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.EXPIRED, TaskStatus.DEAD_LETTER}
    ),
    TaskStatus.WAITING_CALLBACK: frozenset(
        {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.EXPIRED, TaskStatus.FAILED}
    ),
}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Return whether a worker may make one durable state transition."""
    if current == target:
        return not current.is_terminal
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


class TaskEvent(BaseModel):
    """Versioned task fact suitable for persistence and SSE replay."""

    schema_version: int = Field(default=1, ge=1)
    event_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str | None = Field(default=None, min_length=1)
    event_type: TaskEventType
    status: TaskStatus | None = None
    step_index: int | None = Field(default=None, ge=0)
    data: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
