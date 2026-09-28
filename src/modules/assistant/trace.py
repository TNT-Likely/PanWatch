"""Public trace projection shared by live SSE and conversation history."""

from __future__ import annotations

from typing import Any

from src.platform.tasking.contracts import TaskEventType


SSE_EVENT_NAMES = {
    TaskEventType.TASK_CREATED: "task_created",
    TaskEventType.TASK_QUEUED: "task_queued",
    TaskEventType.TASK_STARTED: "run_started",
    TaskEventType.CONTEXT_PREPARED: "context_prepared",
    TaskEventType.STEP_STARTED: "step_started",
    TaskEventType.STEP_PROGRESS: "step_updated",
    TaskEventType.EXTENSION_EVENT: "extension_event",
    TaskEventType.ANSWER_TOKEN: "token",
    TaskEventType.MODEL_USAGE: "model_usage",
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

_HISTORICAL_TRACE_EVENTS = {
    "run_started",
    "context_prepared",
    "step_updated",
    "extension_event",
    "model_usage",
    "tool_call_start",
    "tool_result",
    "approval_required",
    "paused",
    "done",
    "error",
}


def historical_trace_event(
    event_type: str,
    data: dict[str, Any] | None,
    sequence: int,
) -> dict[str, Any] | None:
    """Return the same compact trace item emitted by the browser stream."""
    try:
        public_name = SSE_EVENT_NAMES.get(TaskEventType(event_type), event_type)
    except ValueError:
        public_name = event_type
    if public_name not in _HISTORICAL_TRACE_EVENTS:
        return None
    public_data = dict(data or {})
    if public_name == "done":
        public_data.pop("content", None)
        public_data.pop("result", None)
    return {"event": public_name, "data": public_data, "id": sequence}
