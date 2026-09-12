"""Regression tests for the navigation assistant's SSE transport contract."""

import asyncio
import importlib
import json
import time
from types import SimpleNamespace

from pan_agent import EventType, RunResult, RunStatus, RuntimeEvent

import src.modules.assistant.api as assistant_api


class _FakeService:
    """Keeps the HTTP test at the service boundary; no real model/network is used."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.recorded_assistant_messages: list[str] = []
        self.finished: list[tuple[str, str | None]] = []

    def record_user_message(self, _conversation_id, _content):
        return SimpleNamespace(id=11)

    def create_task(self, _conversation_id, _user_message_id):
        return SimpleNamespace(id=12)

    def build_failover_client(self):
        return object()

    def build_runtime(self, _client):
        return self.runtime

    def get_conversation(self, _conversation_id):
        return SimpleNamespace(
            messages=[SimpleNamespace(role="user", content="测试问题")]
        )

    def record_assistant_message(self, _conversation_id, content):
        self.recorded_assistant_messages.append(content)
        return SimpleNamespace(id=13, content=content, created_at=None)

    def finish_task(self, _task_id, result, final_message_id):
        self.finished.append((result.status.value, result.error_code))

    def fail_task(self, _task_id, error_code):
        self.finished.append(("failed", error_code))


class _CompletedRuntime:
    request = None

    async def run(self, request, sink):
        self.request = request
        await sink.publish(RuntimeEvent(type=EventType.RUN_CREATED, run_id="12"))
        return RunResult(run_id="12", status=RunStatus.COMPLETED, answer="已完成")


class _TimedOutRuntime:
    async def run(self, _request, _sink):
        return RunResult(
            run_id="12", status=RunStatus.PARTIAL, answer="", error_code="run_timeout"
        )


class _EmptyCompletedRuntime:
    async def run(self, _request, _sink):
        return RunResult(run_id="12", status=RunStatus.COMPLETED, answer="")


class _HangingRuntime:
    async def run(self, _request, _sink):
        await asyncio.Event().wait()


class _SlowToCancelRuntime:
    """Models a third-party adapter that delays cleanup after cancellation."""

    async def run(self, _request, _sink):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(2)
            raise


class _BlockingRuntime:
    def __init__(self):
        self.started = asyncio.Event()

    async def run(self, _request, sink):
        await sink.publish(RuntimeEvent(type=EventType.RUN_CREATED, run_id="12"))
        self.started.set()
        await asyncio.Event().wait()


class _SetupFailureService(_FakeService):
    def build_runtime(self, _client):
        raise RuntimeError("runtime setup failed")


async def _read_events(response) -> list[tuple[str, dict]]:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    events: list[tuple[str, dict]] = []
    for block in "".join(chunks).strip().split("\n\n"):
        lines = block.splitlines()
        event = next(
            line.removeprefix("event: ") for line in lines if line.startswith("event: ")
        )
        payload = next(
            line.removeprefix("data: ") for line in lines if line.startswith("data: ")
        )
        events.append((event, json.loads(payload)))
    return events


def test_assistant_stream_announces_a_durable_run_without_fake_status():
    """The assistant transport exposes run creation, not fabricated progress copy."""
    service = _FakeService(_CompletedRuntime())

    async def run():
        response = await assistant_api.stream_assistant_message(
            1, assistant_api.SendAssistantMessageCommand(content="测试问题"), service
        )
        return response.headers, await _read_events(response)

    headers, events = asyncio.run(run())

    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"
    assert events[0] == ("run_started", {"task_id": 12})
    assert all(event != "status" for event, _data in events)
    assert events[-1][0] == "done"
    assert events[-1][1]["content"] == "已完成"
    assert service.runtime.request.limits.run_timeout_seconds == 45
    assert service.runtime.request.limits.max_steps == 12
    assert service.runtime.request.limits.max_tool_calls == 24


def test_assistant_messages_prepend_tool_first_instruction():
    prompt = importlib.import_module("src.modules.assistant.prompt")
    messages = prompt.build_assistant_messages(
        [assistant_api.ModelMessage(role="user", content="分析 600519")]
    )

    assert messages[0].role == "system"
    assert messages[0].content == prompt.ASSISTANT_SYSTEM_PROMPT
    assert "主动调用工具" in messages[0].content
    assert "相同工具和参数最多调用一次" in messages[0].content
    assert messages[1].content == "分析 600519"


def test_assistant_stream_surfaces_runtime_timeout_instead_of_saving_empty_reply():
    """A failed/empty runtime result must become an error event, not an invisible done event."""
    service = _FakeService(_TimedOutRuntime())

    async def run():
        response = await assistant_api.stream_assistant_message(
            1, assistant_api.SendAssistantMessageCommand(content="测试问题"), service
        )
        return await _read_events(response)

    events = asyncio.run(run())

    assert events[-1] == (
        "error",
        {"message": "助手响应超时，请稍后重试。", "code": "run_timeout"},
    )
    assert service.recorded_assistant_messages == []
    assert service.finished == [("failed", "run_timeout")]


def test_assistant_stream_rejects_an_empty_completed_reply():
    """A provider that claims success without content must not create an invisible message."""
    service = _FakeService(_EmptyCompletedRuntime())

    async def run():
        response = await assistant_api.stream_assistant_message(
            1, assistant_api.SendAssistantMessageCommand(content="测试问题"), service
        )
        return await _read_events(response)

    events = asyncio.run(run())

    assert events[-1] == (
        "error",
        {"message": "助手暂时不可用，请稍后重试。", "code": "empty_answer"},
    )
    assert service.recorded_assistant_messages == []
    assert service.finished == [("failed", "empty_answer")]


def test_assistant_stream_starts_work_even_if_the_client_never_reads_the_body():
    """Creating a durable task without starting its worker leaves it stuck in running forever."""
    service = _FakeService(_CompletedRuntime())

    async def run():
        await assistant_api.stream_assistant_message(
            1, assistant_api.SendAssistantMessageCommand(content="测试问题"), service
        )
        async with asyncio.timeout(0.1):
            while not service.finished:
                await asyncio.sleep(0)

    asyncio.run(run())

    assert service.recorded_assistant_messages == ["已完成"]
    assert service.finished == [("completed", None)]


def test_assistant_stream_enforces_the_transport_timeout(monkeypatch):
    """A hanging adapter must reach a terminal error at the product timeout, not five seconds later."""
    monkeypatch.setattr(assistant_api, "ASSISTANT_RUN_TIMEOUT_SECONDS", 1)
    service = _FakeService(_HangingRuntime())

    async def run():
        started = time.monotonic()
        response = await assistant_api.stream_assistant_message(
            1, assistant_api.SendAssistantMessageCommand(content="测试问题"), service
        )
        events = await _read_events(response)
        return time.monotonic() - started, events

    elapsed, events = asyncio.run(run())

    assert elapsed < 1.5
    assert events[-1] == (
        "error",
        {"message": "助手响应超时，请稍后重试。", "code": "transport_timeout"},
    )
    assert service.finished == [("failed", "transport_timeout")]


def test_assistant_stream_does_not_wait_for_a_slow_to_cancel_adapter(monkeypatch):
    """The client must receive the timeout at the deadline even if adapter cancellation is slow."""
    monkeypatch.setattr(assistant_api, "ASSISTANT_RUN_TIMEOUT_SECONDS", 1)
    service = _FakeService(_SlowToCancelRuntime())

    async def run():
        started = time.monotonic()
        response = await assistant_api.stream_assistant_message(
            1, assistant_api.SendAssistantMessageCommand(content="测试问题"), service
        )
        events = await _read_events(response)
        return time.monotonic() - started, events

    elapsed, events = asyncio.run(run())

    assert elapsed < 1.5
    assert events[-1][1]["code"] == "transport_timeout"
    assert service.finished == [("failed", "transport_timeout")]


def test_assistant_stream_closes_a_task_when_client_disconnects_after_run_start():
    """Closing the body after run creation must cancel and terminally persist the worker."""
    runtime = _BlockingRuntime()
    service = _FakeService(runtime)

    async def run():
        response = await assistant_api.stream_assistant_message(
            1, assistant_api.SendAssistantMessageCommand(content="测试问题"), service
        )
        iterator = response.body_iterator
        first = await anext(iterator)
        await asyncio.wait_for(runtime.started.wait(), timeout=0.1)
        await iterator.aclose()
        async with asyncio.timeout(0.1):
            while not service.finished:
                await asyncio.sleep(0)
        return first

    first = asyncio.run(run())

    assert "event: run_started" in first
    assert service.finished == [("failed", "cancelled")]


def test_assistant_stream_closes_the_task_when_runtime_setup_fails():
    """Task setup failures must not escape as a 500 after the task was persisted as running."""
    service = _SetupFailureService(_CompletedRuntime())

    async def run():
        response = await assistant_api.stream_assistant_message(
            1, assistant_api.SendAssistantMessageCommand(content="测试问题"), service
        )
        return await _read_events(response)

    events = asyncio.run(run())

    assert events == [
        (
            "error",
            {
                "message": "助手任务执行失败，请稍后重试。",
                "code": "transport_setup_failed",
            },
        )
    ]
    assert service.finished == [("failed", "transport_setup_failed")]
