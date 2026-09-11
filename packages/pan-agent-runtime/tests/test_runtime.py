import asyncio
import time

from pan_agent import (
    AgentRuntime,
    EventType,
    ModelMessage,
    ModelTurn,
    RunLimits,
    RunRequest,
    RunStatus,
    ToolCall,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
)


class CollectingSink:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class FixedModel:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.received_messages = []

    async def run_turn(self, messages, _tools, _emit_token):
        self.received_messages.append([message.model_copy(deep=True) for message in messages])
        return next(self.turns)


def request(**kwargs):
    return RunRequest(
        run_id="run-1",
        messages=[ModelMessage(role="user", content="hello")],
        limits=RunLimits(**kwargs),
    )


def registry(executor):
    result = ToolRegistry()
    result.register(
        ToolSpec(name="lookup", title="查询", description="read", risk=ToolRisk.READ,
                 input_schema={"type": "object", "properties": {}}),
        executor,
    )
    return result


def test_unknown_tool_is_never_executed_and_returns_partial_result():
    model = FixedModel([ModelTurn(tool_calls=[ToolCall(id="call-1", name="unknown")])])
    sink = CollectingSink()

    result = asyncio.run(AgentRuntime(model, registry(lambda *_: None)).run(request(), sink))

    assert result.status is RunStatus.PARTIAL
    assert result.error_code == "unknown_tool"
    assert EventType.TOOL_STARTED not in [event.type for event in sink.events]


def test_tool_failure_is_retried_once_and_answer_is_completed():
    attempts = 0

    async def flaky_tool(_request, _arguments):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return ToolResult.success(summary="ok", data={}, sources=[{"name": "test"}],
                                  observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC))

    model = FixedModel([
        ModelTurn(tool_calls=[ToolCall(id="call-1", name="lookup")]),
        ModelTurn(content="完成"),
    ])
    sink = CollectingSink()

    result = asyncio.run(AgentRuntime(model, registry(flaky_tool)).run(request(), sink))

    assert attempts == 2
    assert result.status is RunStatus.COMPLETED
    assert result.answer == "完成"
    assert EventType.TOOL_COMPLETED in [event.type for event in sink.events]


def test_tool_result_keeps_the_preceding_call_for_the_next_model_turn():
    async def lookup(_request, _arguments):
        return ToolResult.success(
            summary="查询完成",
            data={"value": 1},
            sources=[{"name": "test"}],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    model = FixedModel([
        ModelTurn(tool_calls=[ToolCall(id="call-1", name="lookup", arguments={"symbol": "CN:601238"})]),
        ModelTurn(content="完成"),
    ])

    result = asyncio.run(AgentRuntime(model, registry(lookup)).run(request(), CollectingSink()))

    next_turn_messages = model.received_messages[1]
    assert result.status is RunStatus.COMPLETED
    assert next_turn_messages[1].role == "assistant"
    assert [call.model_dump() for call in next_turn_messages[1].tool_calls] == [
        {"id": "call-1", "name": "lookup", "arguments": {"symbol": "CN:601238"}}
    ]
    assert next_turn_messages[2].role == "tool"
    assert next_turn_messages[2].tool_call_id == "call-1"


def test_tool_timeout_returns_partial_result():
    async def slow_tool(_request, _arguments):
        await asyncio.sleep(1.1)
        raise AssertionError("timeout expected")

    model = FixedModel([ModelTurn(tool_calls=[ToolCall(id="call-1", name="lookup")])])

    result = asyncio.run(
        AgentRuntime(model, registry(slow_tool)).run(request(tool_timeout_seconds=1), CollectingSink())
    )

    assert result.status is RunStatus.PARTIAL
    assert result.error_code == "tool_timeout"


def test_run_timeout_bounds_a_tool_call_even_when_tool_timeout_is_longer():
    async def slow_tool(_request, _arguments):
        await asyncio.sleep(1.2)
        return ToolResult.success(summary="late", data={}, sources=[], observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC))

    model = FixedModel([ModelTurn(tool_calls=[ToolCall(id="call-1", name="lookup")])])
    started = time.monotonic()
    result = asyncio.run(
        AgentRuntime(model, registry(slow_tool)).run(
            request(run_timeout_seconds=1, tool_timeout_seconds=3), CollectingSink()
        )
    )

    assert result.status is RunStatus.PARTIAL
    assert result.error_code == "run_timeout"
    assert time.monotonic() - started < 1.15
