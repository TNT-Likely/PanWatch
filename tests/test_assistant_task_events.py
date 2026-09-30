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


def test_task_snapshot_contains_usage_and_tool_timing_summary():
    engine, session, repository, task = _repository()

    repository.record_model_usage(
        task.id,
        {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "cached_input_tokens": 10,
            "reasoning_output_tokens": 4,
            "model": "test-model",
            "source": "provider",
        },
    )
    repository.record_tool_started(
        task.id,
        call_id="call-1",
        tool_name="get_quote",
        arguments={"symbol": "600519"},
    )
    repository.record_tool_completed(
        task.id,
        call_id="call-1",
        tool_name="get_quote",
        summary="查询完成",
        ok=True,
        duration_ms=420,
        attempt_count=2,
    )

    snapshot = repository.get_task_snapshot(task.id)

    assert snapshot["usage"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "cached_input_tokens": 10,
        "reasoning_output_tokens": 4,
        "source": "provider",
        "model": "test-model",
    }
    assert snapshot["tools"] == [
        {
            "call_id": "call-1",
            "tool": "get_quote",
            "status": "completed",
            "summary": "查询完成",
            "duration_ms": 420,
            "attempt_count": 2,
            "error_code": None,
            "sources": [],
            "observed_at": None,
        }
    ]

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


def test_task_snapshot_explains_safe_retry_and_restores_cancelled_tool_trace():
    engine, session, repository, task = _repository()
    repository.claim_task(task.id)
    repository.record_tool_started(task.id, call_id="alert-1", tool_name="create_price_alert", arguments={"price": 10})
    repository.cancel_task(task.id)
    snapshot = repository.get_task_snapshot(task.id)
    assert snapshot["can_retry"] is False
    assert snapshot["retry_blocked_reason"] == "tools_already_started"
    assert [event["event"] for event in snapshot["trace"]][-2:] == ["tool_call_start", "cancelled"]
    assert snapshot["tools"][0]["status"] == "started"
    assert repository.latest_task_snapshot(task.conversation_id)["id"] == task.id
    assert snapshot["created_at"].tzinfo is not None
    session.close()
    engine.dispose()


def test_retry_resets_attempt_timing_and_replays_only_the_new_attempt():
    engine, session, repository, task = _repository()
    repository.claim_task(task.id)
    repository.append_task_event(task.id, TaskEventType.STEP_PROGRESS, step_index=4)
    repository.finish_task(task.id, status="failed", final_message_id=None, error_code="run_timeout")
    failed = repository.get_task_snapshot(task.id)
    assert failed["can_retry"] is True
    assert failed["retry_blocked_reason"] is None
    repository.retry_task(task.id)
    queued = repository.get_task_snapshot(task.id)
    assert queued["id"] == failed["id"]
    assert queued["current_step"] == 0
    assert queued["started_at"] is None and queued["finished_at"] is None
    assert queued["duration_ms"] == 0
    assert queued["attempt_event_id"] == queued["last_event_id"]
    repository.claim_task(task.id)
    repository.append_task_event(task.id, TaskEventType.ANSWER_TOKEN, data={"text": "new answer"})
    running = repository.get_task_snapshot(task.id)
    assert [event["event"] for event in running["trace"]] == ["run_started"]
    replay = repository.list_task_events(task.id, after_sequence=int(running["attempt_event_id"]))
    assert [event.event_type for event in replay] == [TaskEventType.TASK_STARTED.value, TaskEventType.ANSWER_TOKEN.value]
    session.close()
    engine.dispose()


def test_conversation_returns_its_latest_task_without_a_browser_marker():
    from src.modules.assistant.service import AssistantService

    engine, session, repository, old = _repository()
    repository.cancel_task(old.id)
    latest = repository.create_task(conversation_id=old.conversation_id, user_message_id=None, context={})
    repository.finish_task(latest.id, status="failed", final_message_id=None, error_code="ai_rate_limited")
    other = repository.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
    service = AssistantService(repository)
    detail = service.get_conversation(old.conversation_id)
    assert detail.latest_task["id"] == latest.id
    assert detail.latest_task["error_code"] == "ai_rate_limited"
    assert service.get_conversation(other.id).latest_task is None
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


def test_m128_adds_trusted_result_columns_idempotently(tmp_path):
    from src.platform.persistence.migrations import (
        _m122_assistant_task_snapshots,
        _m128_assistant_trusted_results,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'trusted-results.db'}")
    with engine.begin() as conn:
        _m122_assistant_task_snapshots(conn)
        _m128_assistant_trusted_results(conn)
        _m128_assistant_trusted_results(conn)
        task_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(assistant_task_runs)"))
        }
        invocation_columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(assistant_tool_invocations)"))
        }
        indexes = {
            row[1]
            for row in conn.execute(text("PRAGMA index_list(assistant_task_runs)"))
        }
    engine.dispose()

    assert {"result_schema_version", "result_data"} <= task_columns
    assert {"observed_at", "result_data"} <= invocation_columns
    assert "ix_assistant_task_run_final_message" in indexes


def test_event_tailing_uses_compact_status_without_rebuilding_a_full_snapshot(monkeypatch):
    from src.modules.assistant.event_stream import subscribe_task_events
    from src.modules.assistant.repository import AssistantRepository

    engine, session, repository, task = _repository()
    repository.claim_task(task.id)
    repository.complete_task_with_message(task.id, task.conversation_id, "完整结果")

    def forbidden_snapshot(*args, **kwargs):
        raise AssertionError("SSE tailing must not reload trace, approvals and tool history")

    monkeypatch.setattr(AssistantRepository, "get_task_snapshot", forbidden_snapshot)

    async def run():
        return [item async for item in subscribe_task_events(task.id, session_factory=sessionmaker(bind=engine))]

    try:
        wire = "".join(asyncio.run(run()))
        assert "event: done" in wire
        assert "完整结果" in wire
    finally:
        session.close()
        engine.dispose()


def test_event_tailing_reports_a_missing_task():
    from src.modules.assistant.event_stream import _load_task_events
    import pytest

    engine, session, _, _ = _repository()
    try:
        with pytest.raises(LookupError, match="助手任务不存在"):
            _load_task_events(999999, 0, sessionmaker(bind=engine))
    finally:
        session.close()
        engine.dispose()
