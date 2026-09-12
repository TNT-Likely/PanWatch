import asyncio
import time

from pan_agent import (
    AgentRuntime,
    ApprovalDecision,
    EventType,
    ModelMessage,
    ModelTurn,
    PendingApproval,
    RunLimits,
    RunRequest,
    RunStatus,
    ToolCall,
    ToolPermissionDecision,
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
        self.received_messages.append(
            [message.model_copy(deep=True) for message in messages]
        )
        return next(self.turns)


class AskPolicy:
    def is_tool_visible(self, _request, _tool):
        return True

    async def decide(self, _request, _tool, _call):
        return ToolPermissionDecision.ask("需要用户确认")


class DenyPolicy:
    def is_tool_visible(self, _request, _tool):
        return True

    async def decide(self, _request, _tool, _call):
        return ToolPermissionDecision.deny("not allowed")


def request(**kwargs):
    return RunRequest(
        run_id="run-1",
        messages=[ModelMessage(role="user", content="hello")],
        limits=RunLimits(**kwargs),
    )


def registry(executor):
    result = ToolRegistry()
    result.register(
        ToolSpec(
            name="lookup",
            title="查询",
            description="read",
            risk=ToolRisk.READ,
            input_schema={"type": "object", "properties": {}},
        ),
        executor,
    )
    return result


def write_registry(executor):
    result = ToolRegistry()
    result.register(
        ToolSpec(
            name="write_note",
            title="写入备注",
            description="write",
            risk=ToolRisk.WRITE,
            input_schema={"type": "object", "properties": {}},
        ),
        executor,
    )
    return result


def test_unknown_tool_is_never_executed_and_returns_partial_result():
    model = FixedModel([ModelTurn(tool_calls=[ToolCall(id="call-1", name="unknown")])])
    sink = CollectingSink()

    result = asyncio.run(
        AgentRuntime(model, registry(lambda *_: None)).run(request(), sink)
    )

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
        return ToolResult.success(
            summary="ok",
            data={},
            sources=[{"name": "test"}],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    model = FixedModel(
        [
            ModelTurn(tool_calls=[ToolCall(id="call-1", name="lookup")]),
            ModelTurn(content="完成"),
        ]
    )
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

    model = FixedModel(
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="call-1", name="lookup", arguments={"symbol": "CN:601238"}
                    )
                ]
            ),
            ModelTurn(content="完成"),
        ]
    )

    result = asyncio.run(
        AgentRuntime(model, registry(lookup)).run(request(), CollectingSink())
    )

    next_turn_messages = model.received_messages[1]
    assert result.status is RunStatus.COMPLETED
    assert next_turn_messages[1].role == "assistant"
    assert [call.model_dump() for call in next_turn_messages[1].tool_calls] == [
        {"id": "call-1", "name": "lookup", "arguments": {"symbol": "CN:601238"}}
    ]
    assert next_turn_messages[2].role == "tool"
    assert next_turn_messages[2].tool_call_id == "call-1"


def test_required_tool_choice_repairs_a_text_only_turn_without_leaking_text():
    class RequiredModel:
        def __init__(self):
            self.turns = iter(
                [
                    ModelTurn(content="我已经更新提醒"),
                    ModelTurn(
                        tool_calls=[
                            ToolCall(
                                id="call-1",
                                name="write_note",
                                arguments={},
                            )
                        ]
                    ),
                    ModelTurn(content="提醒已成功更新。"),
                ]
            )
            self.tool_choices = []
            self.received_messages = []

        async def run_turn(
            self, messages, _tools, emit_token, tool_choice=None
        ):
            self.received_messages.append(
                [message.model_copy(deep=True) for message in messages]
            )
            self.tool_choices.append(tool_choice)
            turn = next(self.turns)
            if turn.content and tool_choice != "required":
                await emit_token(turn.content)
            return turn

    async def write_note(_request, _arguments):
        return ToolResult.success(
            summary="提醒已更新",
            data={},
            sources=[],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    model = RequiredModel()
    request_with_required_tool = RunRequest(
        run_id="required-run",
        messages=[ModelMessage(role="user", content="修改提醒")],
        context={"tool_choice": "required"},
    )
    registry = write_registry(write_note)
    sink = CollectingSink()

    result = asyncio.run(AgentRuntime(model, registry).run(request_with_required_tool, sink))

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "提醒已成功更新。"
    assert model.tool_choices == ["required", "required", None]
    assert model.received_messages[1][-1].role == "system"
    assert "必须调用可用的写入工具" in model.received_messages[1][-1].content
    assert [
        event.data.get("token")
        for event in sink.events
        if event.type is EventType.ANSWER_TOKEN
    ] == ["提醒已成功更新。"]


def test_required_tool_choice_returns_stable_error_after_one_repair_attempt():
    class TextOnlyModel:
        async def run_turn(self, _messages, _tools, _emit_token, tool_choice=None):
            assert tool_choice == "required"
            return ModelTurn(content="提醒已成功更新。")

    request_with_required_tool = RunRequest(
        run_id="required-run",
        messages=[ModelMessage(role="user", content="修改提醒")],
        context={"tool_choice": "required"},
    )

    result = asyncio.run(
        AgentRuntime(TextOnlyModel(), write_registry(lambda *_: None)).run(
            request_with_required_tool, CollectingSink()
        )
    )

    assert result.status is RunStatus.PARTIAL
    assert result.error_code == "required_tool_call_missing"


def test_tool_timeout_returns_partial_result():
    async def slow_tool(_request, _arguments):
        await asyncio.sleep(1.1)
        raise AssertionError("timeout expected")

    model = FixedModel([ModelTurn(tool_calls=[ToolCall(id="call-1", name="lookup")])])

    result = asyncio.run(
        AgentRuntime(model, registry(slow_tool)).run(
            request(tool_timeout_seconds=1), CollectingSink()
        )
    )

    assert result.status is RunStatus.PARTIAL
    assert result.error_code == "tool_timeout"


def test_run_timeout_bounds_a_tool_call_even_when_tool_timeout_is_longer():
    async def slow_tool(_request, _arguments):
        await asyncio.sleep(1.2)
        return ToolResult.success(
            summary="late",
            data={},
            sources=[],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

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


def test_ask_policy_pauses_without_executing_and_returns_a_checkpoint():
    executor_calls = 0

    async def recording_executor(_request, _arguments):
        nonlocal executor_calls
        executor_calls += 1
        return ToolResult.success(
            summary="written",
            data={},
            sources=[],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    tools = write_registry(recording_executor)
    model = FixedModel(
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(id="call-1", name="write_note", arguments={"text": "x"})
                ]
            )
        ]
    )

    result = asyncio.run(
        AgentRuntime(model, tools, policy=AskPolicy()).run(request(), CollectingSink())
    )

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert executor_calls == 0
    assert result.checkpoint is not None
    assert result.checkpoint.pending_approvals == [
        PendingApproval(
            call_id="call-1",
            tool_name="write_note",
            risk=ToolRisk.WRITE,
            arguments={"text": "x"},
        )
    ]


def test_repeated_identical_tool_calls_are_stopped_before_the_run_can_loop_forever():
    executor_calls = 0

    async def recording_executor(_request, _arguments):
        nonlocal executor_calls
        executor_calls += 1
        return ToolResult.success(
            summary="查询完成",
            data={"value": 1},
            sources=[],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    class RepeatingModel:
        def __init__(self):
            self.calls = 0

        async def run_turn(self, _messages, _tools, _emit_token):
            self.calls += 1
            return ModelTurn(
                tool_calls=[
                    ToolCall(id=f"call-{self.calls}", name="lookup", arguments={})
                ]
            )

    model = RepeatingModel()
    result = asyncio.run(
        AgentRuntime(model, registry(recording_executor)).run(
            request(max_steps=12, max_tool_calls=24), CollectingSink()
        )
    )

    assert result.status is RunStatus.PARTIAL
    assert result.error_code == "repeated_tool_call"
    assert model.calls == 2
    assert executor_calls == 1


def test_resume_approved_call_executes_once_on_a_fresh_runtime_and_completes():
    executor_calls = 0

    async def recording_executor(_request, _arguments):
        nonlocal executor_calls
        executor_calls += 1
        return ToolResult.success(
            summary="written",
            data={},
            sources=[],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    tools = write_registry(recording_executor)
    paused = asyncio.run(
        AgentRuntime(
            FixedModel(
                [
                    ModelTurn(
                        tool_calls=[
                            ToolCall(
                                id="call-1", name="write_note", arguments={"text": "x"}
                            )
                        ]
                    )
                ]
            ),
            tools,
            policy=AskPolicy(),
        ).run(request(), CollectingSink())
    )
    assert paused.checkpoint is not None

    resumed_model = FixedModel([ModelTurn(content="已写入")])
    result = asyncio.run(
        AgentRuntime(resumed_model, tools, policy=AskPolicy()).resume(
            request(),
            paused.checkpoint,
            {"call-1": ApprovalDecision.APPROVED},
            CollectingSink(),
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "已写入"
    assert executor_calls == 1
    assert resumed_model.received_messages[0][-1].tool_call_id == "call-1"


def test_resume_rejected_call_does_not_execute_and_returns_rejection_to_the_model():
    executor_calls = 0

    async def recording_executor(_request, _arguments):
        nonlocal executor_calls
        executor_calls += 1
        raise AssertionError("rejected calls must not execute")

    tools = write_registry(recording_executor)
    paused = asyncio.run(
        AgentRuntime(
            FixedModel(
                [ModelTurn(tool_calls=[ToolCall(id="call-1", name="write_note")])]
            ),
            tools,
            policy=AskPolicy(),
        ).run(request(), CollectingSink())
    )
    assert paused.checkpoint is not None

    resumed_model = FixedModel([ModelTurn(content="无法写入")])
    result = asyncio.run(
        AgentRuntime(resumed_model, tools, policy=AskPolicy()).resume(
            request(),
            paused.checkpoint,
            {"call-1": ApprovalDecision.REJECTED},
            CollectingSink(),
        )
    )

    tool_message = resumed_model.received_messages[0][-1]
    assert result.status is RunStatus.COMPLETED
    assert executor_calls == 0
    assert (
        __import__("json").loads(tool_message.content)["error_code"]
        == "approval_rejected"
    )


def test_denied_call_does_not_execute_and_the_model_can_return_a_safe_alternative():
    executor_calls = 0

    async def recording_executor(_request, _arguments):
        nonlocal executor_calls
        executor_calls += 1
        raise AssertionError("denied calls must not execute")

    model = FixedModel(
        [
            ModelTurn(tool_calls=[ToolCall(id="call-1", name="write_note")]),
            ModelTurn(content="我无法执行写入操作。"),
        ]
    )

    result = asyncio.run(
        AgentRuntime(
            model, write_registry(recording_executor), policy=DenyPolicy()
        ).run(request(), CollectingSink())
    )

    assert result.status is RunStatus.COMPLETED
    assert executor_calls == 0
    assert (
        __import__("json").loads(model.received_messages[1][-1].content)["error_code"]
        == "permission_denied"
    )


def test_ask_policy_emits_one_approval_event_for_multiple_calls_in_original_order():
    async def recording_executor(_request, _arguments):
        raise AssertionError("pending calls must not execute")

    sink = CollectingSink()
    model = FixedModel(
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="call-1", name="write_note", arguments={"text": "first"}
                    ),
                    ToolCall(
                        id="call-2", name="write_note", arguments={"text": "second"}
                    ),
                ]
            )
        ]
    )

    result = asyncio.run(
        AgentRuntime(model, write_registry(recording_executor), policy=AskPolicy()).run(
            request(), sink
        )
    )

    approvals = [
        event for event in sink.events if event.type is EventType.APPROVAL_REQUIRED
    ]
    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert len(approvals) == 1
    assert [call["call_id"] for call in approvals[0].data["calls"]] == [
        "call-1",
        "call-2",
    ]
    assert [call["risk"] for call in approvals[0].data["calls"]] == ["write", "write"]


def test_resume_executes_decided_call_and_repauses_remaining_approvals():
    executed = []

    async def recording_executor(_request, arguments):
        executed.append(arguments["text"])
        return ToolResult.success(
            summary=f"已写入 {arguments['text']}",
            data={},
            sources=[],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    tools = write_registry(recording_executor)
    paused = asyncio.run(
        AgentRuntime(
            FixedModel(
                [
                    ModelTurn(
                        tool_calls=[
                            ToolCall(
                                id="call-1",
                                name="write_note",
                                arguments={"text": "first"},
                            ),
                            ToolCall(
                                id="call-2",
                                name="write_note",
                                arguments={"text": "second"},
                            ),
                        ]
                    )
                ]
            ),
            tools,
            policy=AskPolicy(),
        ).run(request(), CollectingSink())
    )

    result = asyncio.run(
        AgentRuntime(FixedModel([]), tools, policy=AskPolicy()).resume(
            request(),
            paused.checkpoint,
            {"call-1": ApprovalDecision.APPROVED},
            CollectingSink(),
        )
    )

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert [item.call_id for item in result.pending_approvals] == ["call-2"]
    assert executed == ["first"]


def test_resume_rejects_decisions_for_unknown_pending_calls():
    async def recording_executor(_request, _arguments):
        return ToolResult.success(
            summary="written",
            data={},
            sources=[],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    tools = write_registry(recording_executor)
    paused = asyncio.run(
        AgentRuntime(
            FixedModel(
                [ModelTurn(tool_calls=[ToolCall(id="call-1", name="write_note")])]
            ),
            tools,
            policy=AskPolicy(),
        ).run(request(), CollectingSink())
    )
    assert paused.checkpoint is not None

    with __import__("pytest").raises(ValueError, match="pending approval call IDs"):
        asyncio.run(
            AgentRuntime(FixedModel([]), tools, policy=AskPolicy()).resume(
                request(),
                paused.checkpoint,
                {"unexpected": ApprovalDecision.APPROVED},
                CollectingSink(),
            )
        )
