from datetime import UTC

import pytest


def test_task_status_exposes_terminal_state_and_transition_rules():
    from src.platform.tasking.contracts import TaskStatus, can_transition

    assert can_transition(TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
    assert can_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.RUNNING)
    assert not can_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)
    assert TaskStatus.COMPLETED.is_terminal is True
    assert TaskStatus.WAITING_RETRY.is_terminal is False


def test_task_event_is_versioned_and_uses_an_aware_timestamp():
    from src.platform.tasking.contracts import TaskEvent, TaskEventType, TaskStatus

    event = TaskEvent(
        task_id="task-42",
        event_type=TaskEventType.CHECKPOINT_SAVED,
        status=TaskStatus.RUNNING,
        step_index=3,
        data={"checkpoint_id": "cp-1"},
    )

    assert event.schema_version == 1
    assert event.event_id
    assert event.occurred_at.tzinfo is UTC
    assert event.event_type.value == "checkpoint_saved"


def test_task_event_rejects_empty_task_id():
    from src.platform.tasking.contracts import TaskEvent, TaskEventType

    with pytest.raises(ValueError):
        TaskEvent(task_id="", event_type=TaskEventType.TASK_CREATED)
