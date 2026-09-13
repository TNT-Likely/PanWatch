"""Durable task event log behavior for P1 background execution."""

import asyncio
import json
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.tasking.contracts import TaskEventType, TaskStatus


def _repository():
    from src.modules.assistant.repository import AssistantRepository

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repository = AssistantRepository(session)
    conversation = repository.create_conversation(
        stock_symbol=None, stock_market=None, initial_context=None
    )
    task = repository.create_task(
        conversation_id=conversation.id, user_message_id=None, context={}
    )
    return engine, session, repository, task


def test_create_task_appends_ordered_events_and_starts_queued():
    engine, session, repository, task = _repository()

    events = repository.list_task_events(task.id)

    assert task.status == TaskStatus.QUEUED.value
    assert [event.sequence for event in events] == [1, 2]
    assert [event.event_type for event in events] == [
        TaskEventType.TASK_CREATED.value,
        TaskEventType.TASK_QUEUED.value,
    ]
    assert repository.get_task_snapshot(task.id)["last_event_id"] == "2"

    session.close()
    engine.dispose()


def test_append_task_event_can_be_replayed_after_a_cursor():
    engine, session, repository, task = _repository()

    repository.append_task_event(
        task.id,
        TaskEventType.STEP_PROGRESS,
        status=TaskStatus.RUNNING,
        step_index=1,
        data={"summary": "正在查询"},
    )
    repository.append_task_event(
        task.id,
        TaskEventType.TOOL_COMPLETED,
        status=TaskStatus.RUNNING,
        step_index=1,
        data={"tool": "get_stock_quote", "ok": True},
    )

    events = repository.list_task_events(task.id, after_sequence=2)

    assert [event.sequence for event in events] == [3, 4]
    assert events[0].data == {"summary": "正在查询"}
    assert events[1].event_id

    session.close()
    engine.dispose()


def test_concurrent_task_event_appends_are_serialized(tmp_path):
    from src.modules.assistant.repository import AssistantRepository

    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-events.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    setup = factory()
    repository = AssistantRepository(setup)
    conversation = repository.create_conversation(
        stock_symbol=None, stock_market=None, initial_context=None
    )
    task = repository.create_task(
        conversation_id=conversation.id, user_message_id=None, context={}
    )
    setup.close()

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def append_event(step: int) -> None:
        db = factory()
        try:
            barrier.wait()
            AssistantRepository(db).append_task_event(
                task.id, TaskEventType.STEP_PROGRESS, data={"step": step}
            )
        except Exception as exc:  # pragma: no cover - assertion reports the cause
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=append_event, args=(step,)) for step in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    check = factory()
    events = AssistantRepository(check).list_task_events(task.id)
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    check.close()
    engine.dispose()


def test_task_event_stream_replays_and_stops_at_terminal_state():
    from src.modules.assistant.event_stream import subscribe_task_events

    engine, session, repository, task = _repository()
    repository.finish_task(
        task.id,
        status="completed",
        final_message_id=42,
        event_data={"message_id": 42, "content": "完成"},
    )

    async def collect():
        return [item async for item in subscribe_task_events(task.id, session_factory=sessionmaker(bind=engine))]

    blocks = asyncio.run(collect())
    parsed = [block for block in blocks if not block.startswith(":")]

    assert len(parsed) == 3
    assert parsed[-1].startswith("id: 3\nevent: done\n")
    assert json.loads(parsed[-1].split("data: ", 1)[1].splitlines()[0])["content"] == "完成"

    session.close()
    engine.dispose()


def test_task_event_stream_closes_after_approval_pause():
    from src.modules.assistant.event_stream import subscribe_task_events

    engine, session, repository, task = _repository()
    task.status = TaskStatus.WAITING_APPROVAL.value
    session.commit()
    repository.append_task_event(
        task.id,
        TaskEventType.TASK_PAUSED,
        status=TaskStatus.WAITING_APPROVAL,
        data={"reason": "approval_required"},
    )

    async def collect():
        return [item async for item in subscribe_task_events(
            task.id, session_factory=sessionmaker(bind=engine)
        )]

    blocks = asyncio.run(collect())
    assert blocks[-1].startswith("id: 3\nevent: paused\n")

    session.close()
    engine.dispose()


def test_claim_task_only_allows_one_worker_to_start_queued_work():
    engine, session, repository, task = _repository()

    assert repository.claim_task(task.id) is True
    assert repository.claim_task(task.id) is False
    assert repository.get_task_snapshot(task.id)["status"] == TaskStatus.RUNNING.value
    assert [event.event_type for event in repository.list_task_events(task.id)] == [
        TaskEventType.TASK_CREATED.value,
        TaskEventType.TASK_QUEUED.value,
        TaskEventType.TASK_STARTED.value,
    ]

    session.close()
    engine.dispose()


def test_completion_does_not_leave_message_after_cancellation():
    engine, session, repository, task = _repository()
    repository.claim_task(task.id)
    repository.cancel_task(task.id)
    repository.finish_task(
        task.id, status=TaskStatus.FAILED.value, final_message_id=None, error_code="late"
    )

    assert repository.complete_task_with_message(task.id, task.conversation_id, "完成") is None
    assert repository.list_messages(task.conversation_id) == []
    assert repository.get_task_snapshot(task.id)["status"] == TaskStatus.CANCELLED.value

    session.close()
    engine.dispose()


def test_retry_does_not_replay_completed_or_tool_side_effect_tasks():
    engine, session, repository, task = _repository()
    repository.finish_task(task.id, status=TaskStatus.COMPLETED.value, final_message_id=42)
    assert repository.retry_task(task.id).status == TaskStatus.COMPLETED.value

    failed = repository.create_task(
        conversation_id=task.conversation_id, user_message_id=None, context={}
    )
    repository.finish_task(
        failed.id, status=TaskStatus.FAILED.value, final_message_id=None, error_code="x"
    )
    repository.record_tool_completed(
        failed.id, call_id="call-1", tool_name="write_tool", summary="已执行"
    )
    assert repository.retry_task(failed.id).status == TaskStatus.FAILED.value

    crashed = repository.create_task(
        conversation_id=task.conversation_id, user_message_id=None, context={}
    )
    repository.claim_task(crashed.id)
    repository.record_tool_started(
        crashed.id,
        call_id="call-crashed",
        tool_name="write_tool",
        arguments={"rule_id": 1},
    )
    repository.finish_task(
        crashed.id, status=TaskStatus.FAILED.value, final_message_id=None, error_code="x"
    )
    assert repository.retry_task(crashed.id).status == TaskStatus.FAILED.value

    session.close()
    engine.dispose()


def test_m126_creates_event_store_idempotently(tmp_path):
    from src.platform.persistence.migrations import _m126_assistant_task_events

    engine = create_engine(f"sqlite:///{tmp_path / 'task-events.db'}")
    with engine.begin() as conn:
        _m126_assistant_task_events(conn)
        _m126_assistant_task_events(conn)
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }

    engine.dispose()

    assert "assistant_task_events" in tables
