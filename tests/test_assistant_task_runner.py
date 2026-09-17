"""The in-process P1 worker persists runtime facts before delivery."""

import asyncio
from types import SimpleNamespace

from pan_agent import EventType, RunResult, RunStatus, RuntimeEvent
from sqlalchemy.orm import sessionmaker

from src.platform.tasking.contracts import TaskEventType


def test_durable_runtime_sink_batches_answer_and_preserves_event_order():
    from src.modules.assistant.task_runner import DurableRuntimeEventSink
    from tests.test_assistant_task_events import _repository

    engine, session, repository, task = _repository()

    class Service:
        _repository = repository

        def record_tool_started(self, _task_id, data):
            repository.record_tool_started(
                task.id,
                call_id=data["call_id"],
                tool_name=data["tool"],
                arguments=data["arguments"],
            )

        def record_tool_completion(self, _task_id, data):
            repository.record_tool_completed(
                task.id,
                call_id=data["call_id"],
                tool_name=data["tool"],
                summary=data["summary"],
            )

    sink = DurableRuntimeEventSink(Service(), task.id)

    async def publish_events():
        await sink.publish(
            RuntimeEvent(
                type=EventType.ANSWER_TOKEN,
                run_id=str(task.id),
                data={"token": "完成"},
            )
        )
        await sink.publish(
            RuntimeEvent(
                type=EventType.TOOL_STARTED,
                run_id=str(task.id),
                data={"call_id": "call-1", "tool": "get_quote", "arguments": {"symbol": "600519"}},
            )
        )
        await sink.publish(
            RuntimeEvent(
                type=EventType.MODEL_USAGE,
                run_id=str(task.id),
                data={"input_tokens": 120, "output_tokens": 30, "source": "provider"},
            )
        )

    asyncio.run(publish_events())
    events = repository.list_task_events(task.id, after_sequence=2)

    assert [event.event_type for event in events] == [
        TaskEventType.ANSWER_TOKEN.value,
        TaskEventType.TOOL_STARTED.value,
        TaskEventType.MODEL_USAGE.value,
    ]
    assert events[0].data == {"text": "完成"}
    assert events[1].data["name"] == "get_quote"
    assert events[2].data["input_tokens"] == 120

    session.close()
    engine.dispose()


def test_durable_runtime_sink_does_not_write_each_answer_token():
    from src.modules.assistant.task_runner import DurableRuntimeEventSink
    from tests.test_assistant_task_events import _repository

    engine, session, repository, task = _repository()

    class Service:
        _repository = repository

    sink = DurableRuntimeEventSink(Service(), task.id)

    async def publish_tokens():
        for token in ("你", "好"):
            await sink.publish(
                RuntimeEvent(
                    type=EventType.ANSWER_TOKEN,
                    run_id=str(task.id),
                    data={"token": token},
                )
            )

    asyncio.run(publish_tokens())
    assert repository.list_task_events(task.id, after_sequence=2) == []

    asyncio.run(sink.flush())
    events = repository.list_task_events(task.id, after_sequence=2)
    assert len(events) == 1
    assert events[0].event_type == TaskEventType.ANSWER_TOKEN.value
    assert events[0].data == {"text": "你好"}

    session.close()
    engine.dispose()


def test_durable_runtime_sink_persists_extension_facts():
    from src.modules.assistant.task_runner import DurableRuntimeEventSink
    from tests.test_assistant_task_events import _repository

    engine, session, repository, task = _repository()

    class Service:
        _repository = repository

    sink = DurableRuntimeEventSink(Service(), task.id)

    async def publish_research():
        await sink.publish(
            RuntimeEvent(
                type=EventType.EXTENSION_EVENT,
                run_id=str(task.id),
                data={
                    "extension": "tool_research",
                    "event": "started",
                    "data": {"mode": "shadow", "query_hash": "abc123"},
                },
            )
        )
        await sink.publish(
            RuntimeEvent(
                type=EventType.EXTENSION_EVENT,
                run_id=str(task.id),
                data={
                    "extension": "tool_research",
                    "event": "completed",
                    "data": {"selected_tools": ["get_stock_quote"]},
                },
            )
        )

    asyncio.run(publish_research())
    events = repository.list_task_events(task.id, after_sequence=2)

    assert [event.event_type for event in events] == [
        TaskEventType.EXTENSION_EVENT.value,
        TaskEventType.EXTENSION_EVENT.value,
    ]
    assert events[1].data["data"]["selected_tools"] == ["get_stock_quote"]

    session.close()
    engine.dispose()


def test_runner_executes_from_queued_snapshot_and_persists_terminal_event(monkeypatch):
    from src.modules.assistant.task_runner import AssistantTaskRunner
    from tests.test_assistant_task_events import _repository

    engine, session, repository, task = _repository()
    task_id = task.id
    conversation_id = task.conversation_id
    session.close()
    check_session = sessionmaker(bind=engine)()
    from src.modules.assistant.repository import AssistantRepository

    repository = AssistantRepository(check_session)

    class Runtime:
        async def run(self, _request, sink):
            await sink.publish(
                RuntimeEvent(
                    type=EventType.ANSWER_TOKEN,
                    run_id=str(task_id),
                    data={"token": "完成"},
                )
            )
            return RunResult(
                run_id=str(task_id), status=RunStatus.COMPLETED, answer="完成"
            )

    class Service:
        def __init__(self, repo):
            self._repository = repo

        async def prepare_context(self, _conversation_id):
            return None

        def build_failover_client(self):
            return object()

        def build_runtime(self, _client):
            return Runtime()

        def get_conversation(self, conversation_id):
            return SimpleNamespace(messages=[])

        def complete_task_with_message(self, task_id, conversation_id, content):
            return self._repository.complete_task_with_message(
                task_id,
                conversation_id,
                content,
            )

    monkeypatch.setattr(
        "src.modules.assistant.task_runner.AssistantService", Service
    )
    runner = AssistantTaskRunner(session_factory=lambda: sessionmaker(bind=engine)())

    async def run():
        runner.start_message(task_id, conversation_id)
        for _ in range(20):
            if repository.get_task_snapshot(task_id)["status"] == "completed":
                return
            await asyncio.sleep(0.01)
        raise AssertionError("runner did not finish")

    asyncio.run(run())
    events = repository.list_task_events(task_id, after_sequence=2)

    assert [event.event_type for event in events] == [
        TaskEventType.TASK_STARTED.value,
        TaskEventType.ANSWER_TOKEN.value,
        TaskEventType.TASK_COMPLETED.value,
    ]
    assert repository.get_task_snapshot(task_id)["status"] == "completed"

    check_session.close()
    engine.dispose()


def test_runner_recovery_requeues_queued_tasks_and_fails_stale_running_tasks(monkeypatch):
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.task_runner import AssistantTaskRunner
    from tests.test_assistant_task_events import _repository

    engine, session, repository, queued = _repository()
    running = repository.create_task(
        conversation_id=queued.conversation_id, user_message_id=None, context={}
    )
    repository.mark_task_running(running.id)
    queued_id = queued.id
    running_id = running.id
    conversation_id = queued.conversation_id
    session.close()

    check_session = sessionmaker(bind=engine)()
    repository = AssistantRepository(check_session)
    runner = AssistantTaskRunner(session_factory=lambda: sessionmaker(bind=engine)())
    started: list[tuple[int, int]] = []
    monkeypatch.setattr(
        runner,
        "start_message",
        lambda task_id, conversation_id: started.append((task_id, conversation_id)),
    )

    asyncio.run(runner.recover_pending())

    assert started == [(queued_id, conversation_id)]
    assert repository.get_task_snapshot(running_id)["status"] == "failed"
    assert repository.get_task_snapshot(running_id)["error_code"] == "worker_restarted"

    check_session.close()
    engine.dispose()
