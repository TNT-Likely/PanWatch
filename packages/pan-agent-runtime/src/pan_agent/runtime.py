"""Bounded, observable execution loop for a host-supplied model and tools."""

from __future__ import annotations

import asyncio
import json
import time

from .contracts import (
    AgentCheckpoint,
    ApprovalDecision,
    EventType,
    ModelMessage,
    PendingApproval,
    PermissionMode,
    RunRequest,
    RunResult,
    RunStatus,
    RuntimeEvent,
    ToolCall,
    ToolResult,
)
from .errors import UnknownTool
from .policy import ReadOnlyToolPolicy
from .ports import EventSink, ModelPort, ToolPolicy
from .registry import ToolRegistry

_MAX_IDENTICAL_TOOL_CALLS = 2
_REQUIRED_TOOL_CHOICE = "required"
_REQUIRED_TOOL_REPAIR_MESSAGE = (
    "本轮请求需要执行写入操作。不要用自然语言代替操作结果，"
    "必须调用可用的写入工具；如果缺少必要信息，请明确说明。"
)


def _tool_call_fingerprint(call: ToolCall) -> str:
    """Create a stable key for detecting a model repeating one tool request."""
    return f"{call.name}:{json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"


class AgentRuntime:
    """A serial tool loop with hard limits and durable approval pauses.

    Persistence and browser transport remain host concerns. The runtime only
    owns provider-neutral messages, permission decisions and portable events.
    """

    def __init__(
        self, model: ModelPort, tools: ToolRegistry, policy: ToolPolicy | None = None
    ) -> None:
        self._model = model
        self._tools = tools
        self._policy = policy or ReadOnlyToolPolicy()

    async def run(self, request: RunRequest, sink: EventSink) -> RunResult:
        """Start a new run and publish the durable creation fact."""
        await self._publish(sink, request, EventType.RUN_CREATED)
        return await self._run_loop(
            request,
            sink,
            messages=[message.model_copy(deep=True) for message in request.messages],
            answer="",
            step_index=0,
            tool_calls=0,
            deadline=time.monotonic() + request.limits.run_timeout_seconds,
        )

    async def resume(
        self,
        request: RunRequest,
        checkpoint: AgentCheckpoint,
        decisions: dict[str, ApprovalDecision],
        sink: EventSink,
    ) -> RunResult:
        """Apply supplied decisions and pause again for remaining calls.

        Hosts may present one checkpoint as several independent approval cards.
        A resume therefore accepts any non-empty subset of pending call IDs:
        decided calls execute now while undecided calls stay in a new checkpoint.
        Once the last card is decided, normal model execution continues.
        """
        pending_ids = {pending.call_id for pending in checkpoint.pending_approvals}
        decision_ids = set(decisions)
        if not pending_ids or not decision_ids or not decision_ids <= pending_ids:
            raise ValueError(
                "decisions must contain one or more pending approval call IDs"
            )

        messages = [message.model_copy(deep=True) for message in checkpoint.messages]
        answer = checkpoint.answer
        tool_calls = checkpoint.tool_calls_used
        deadline = time.monotonic() + request.limits.run_timeout_seconds

        remaining_pending: list[PendingApproval] = []
        try:
            for pending in checkpoint.pending_approvals:
                if pending.call_id not in decisions:
                    remaining_pending.append(pending)
                    continue
                call = ToolCall(
                    id=pending.call_id,
                    name=pending.tool_name,
                    arguments=pending.arguments,
                )
                if decisions[pending.call_id] is ApprovalDecision.APPROVED:
                    result, error_code = await self._execute_call(
                        request, sink, call, deadline
                    )
                    if error_code:
                        return await self._finish(
                            sink,
                            request,
                            RunStatus.PARTIAL,
                            answer,
                            tool_calls,
                            error_code,
                        )
                else:
                    result = ToolResult.failure(
                        summary="用户拒绝了此操作",
                        error_code="approval_rejected",
                    )
                    await self._publish_tool_completed(sink, request, call, result)
                self._append_tool_result(messages, call, result)
        except TimeoutError:
            return await self._finish(
                sink, request, RunStatus.PARTIAL, answer, tool_calls, "run_timeout"
            )
        except asyncio.CancelledError:
            return await self._finish(
                sink, request, RunStatus.CANCELLED, answer, tool_calls, "cancelled"
            )
        except Exception:  # noqa: BLE001 - hosts receive a stable terminal runtime result
            return await self._finish(
                sink, request, RunStatus.FAILED, answer, tool_calls, "runtime_failed"
            )

        if remaining_pending:
            next_checkpoint = AgentCheckpoint(
                messages=messages,
                answer=answer,
                step_index=checkpoint.step_index,
                tool_calls_used=tool_calls,
                pending_approvals=remaining_pending,
            )
            await self._publish(
                sink,
                request,
                EventType.APPROVAL_REQUIRED,
                {
                    "calls": [
                        pending_call.model_dump(mode="json")
                        for pending_call in remaining_pending
                    ]
                },
            )
            return RunResult(
                run_id=request.run_id,
                status=RunStatus.WAITING_FOR_APPROVAL,
                answer=answer,
                tool_calls=tool_calls,
                checkpoint=next_checkpoint,
                pending_approvals=remaining_pending,
            )

        return await self._run_loop(
            request,
            sink,
            messages=messages,
            answer=answer,
            step_index=checkpoint.step_index,
            tool_calls=tool_calls,
            deadline=deadline,
        )

    async def _run_loop(
        self,
        request: RunRequest,
        sink: EventSink,
        *,
        messages: list[ModelMessage],
        answer: str,
        step_index: int,
        tool_calls: int,
        deadline: float,
    ) -> RunResult:
        async def emit_token(token: str) -> None:
            nonlocal answer
            answer += token
            await self._publish(sink, request, EventType.ANSWER_TOKEN, {"token": token})

        current_tool_choice: str | None = None

        async def emit_model_token(token: str) -> None:
            # A required-tool turn is an internal action proposal.  Never leak
            # its preamble to the user before the tool has actually run.
            if current_tool_choice == _REQUIRED_TOOL_CHOICE:
                return
            await emit_token(token)

        last_tool_fingerprint = ""
        identical_tool_calls = 0
        required_tool_repair_used = False
        try:
            for current_step in range(step_index + 1, request.limits.max_steps + 1):
                self._ensure_before_deadline(deadline)
                await self._publish(
                    sink,
                    request,
                    EventType.STEP_UPDATED,
                    {"step": current_step, "status": "running"},
                )
                current_tool_choice = self._tool_choice_for_turn(request, messages)
                turn = await self._run_model_turn(
                    request, messages, emit_model_token, deadline, current_tool_choice
                )
                if turn.content and not answer and current_tool_choice != _REQUIRED_TOOL_CHOICE:
                    await emit_token(turn.content)

                if not turn.tool_calls:
                    if current_tool_choice == _REQUIRED_TOOL_CHOICE:
                        if not required_tool_repair_used:
                            required_tool_repair_used = True
                            messages.append(
                                ModelMessage(
                                    role="system",
                                    content=_REQUIRED_TOOL_REPAIR_MESSAGE,
                                )
                            )
                            continue
                        return await self._finish(
                            sink,
                            request,
                            RunStatus.PARTIAL,
                            answer,
                            tool_calls,
                            "required_tool_call_missing",
                        )
                    return await self._finish(
                        sink, request, RunStatus.COMPLETED, answer, tool_calls
                    )

                # An OpenAI-compatible provider needs every result associated
                # with exactly one preceding assistant tool-call turn.
                messages.append(
                    ModelMessage(
                        role="assistant",
                        content=turn.content,
                        tool_calls=[
                            call.model_copy(deep=True) for call in turn.tool_calls
                        ],
                    )
                )
                pending: list[PendingApproval] = []
                for call in turn.tool_calls:
                    if tool_calls >= request.limits.max_tool_calls:
                        return await self._finish(
                            sink,
                            request,
                            RunStatus.PARTIAL,
                            answer,
                            tool_calls,
                            "tool_call_limit",
                        )
                    tool_calls += 1
                    try:
                        tool = self._tools.get(call.name)
                    except UnknownTool:
                        return await self._finish(
                            sink,
                            request,
                            RunStatus.PARTIAL,
                            answer,
                            tool_calls,
                            "unknown_tool",
                        )

                    fingerprint = _tool_call_fingerprint(call)
                    if fingerprint == last_tool_fingerprint:
                        identical_tool_calls += 1
                    else:
                        last_tool_fingerprint = fingerprint
                        identical_tool_calls = 1
                    if identical_tool_calls >= _MAX_IDENTICAL_TOOL_CALLS:
                        return await self._finish(
                            sink,
                            request,
                            RunStatus.PARTIAL,
                            answer,
                            tool_calls,
                            "repeated_tool_call",
                        )

                    decision = await self._policy.decide(request, tool.spec, call)
                    if decision.mode is PermissionMode.ASK:
                        pending.append(
                            PendingApproval(
                                call_id=call.id,
                                tool_name=call.name,
                                risk=tool.spec.risk,
                                arguments=call.arguments,
                            )
                        )
                        continue
                    if decision.mode is PermissionMode.DENY:
                        result = ToolResult.failure(
                            summary="工具权限不足", error_code="permission_denied"
                        )
                        await self._publish_tool_completed(sink, request, call, result)
                        self._append_tool_result(messages, call, result)
                        continue

                    result, error_code = await self._execute_call(
                        request, sink, call, deadline
                    )
                    if error_code:
                        return await self._finish(
                            sink,
                            request,
                            RunStatus.PARTIAL,
                            answer,
                            tool_calls,
                            error_code,
                        )
                    self._append_tool_result(messages, call, result)

                if pending:
                    checkpoint = AgentCheckpoint(
                        messages=messages,
                        answer=answer,
                        step_index=current_step,
                        tool_calls_used=tool_calls,
                        pending_approvals=pending,
                    )
                    await self._publish(
                        sink,
                        request,
                        EventType.APPROVAL_REQUIRED,
                        {
                            "calls": [
                                pending_call.model_dump(mode="json")
                                for pending_call in pending
                            ]
                        },
                    )
                    return RunResult(
                        run_id=request.run_id,
                        status=RunStatus.WAITING_FOR_APPROVAL,
                        answer=answer,
                        tool_calls=tool_calls,
                        checkpoint=checkpoint,
                        pending_approvals=pending,
                    )

            return await self._finish(
                sink, request, RunStatus.PARTIAL, answer, tool_calls, "step_limit"
            )
        except TimeoutError:
            return await self._finish(
                sink, request, RunStatus.PARTIAL, answer, tool_calls, "run_timeout"
            )
        except asyncio.CancelledError:
            return await self._finish(
                sink, request, RunStatus.CANCELLED, answer, tool_calls, "cancelled"
            )
        except Exception:  # noqa: BLE001 - hosts receive a stable terminal runtime result
            return await self._finish(
                sink, request, RunStatus.FAILED, answer, tool_calls, "runtime_failed"
            )

    async def _run_model_turn(
        self,
        request: RunRequest,
        messages,
        emit_token,
        deadline,
        tool_choice: str | None = None,
    ):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            model_tools = self._tools.model_tools(request, self._policy)
            if tool_choice is None:
                return await self._model.run_turn(messages, model_tools, emit_token)
            return await self._model.run_turn(
                messages,
                model_tools,
                emit_token,
                tool_choice=tool_choice,
            )

    @staticmethod
    def _tool_choice_for_turn(request: RunRequest, messages: list[ModelMessage]) -> str | None:
        """Require a tool only for the initial turn of an action run.

        Once a tool result is present, the model must be allowed to produce a
        normal final answer; otherwise ``required`` would force an endless
        second tool call after a successful write.
        """
        if request.context.get("tool_choice") != _REQUIRED_TOOL_CHOICE:
            return None
        if any(message.role == "tool" for message in messages):
            # The first turn is restricted to registered write tools. Once a
            # result exists, restore the host's normal tool visibility so the
            # model can read context and compose a grounded final answer.
            request.context.pop("allowed_tool_names", None)
            return None
        return _REQUIRED_TOOL_CHOICE

    async def _execute_call(
        self, request: RunRequest, sink: EventSink, call: ToolCall, deadline: float
    ) -> tuple[ToolResult, str | None]:
        try:
            self._tools.get(call.name)
        except UnknownTool:
            return ToolResult.failure(
                summary="请求的工具不可用", error_code="unknown_tool"
            ), "unknown_tool"

        await self._publish(
            sink,
            request,
            EventType.TOOL_STARTED,
            {"call_id": call.id, "tool": call.name, "arguments": call.arguments},
        )
        result: ToolResult | None = None
        for attempt in range(request.limits.step_retry_count + 1):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                timeout = min(request.limits.tool_timeout_seconds, remaining)
                async with asyncio.timeout(timeout):
                    result = await self._tools.execute(
                        call.name, request, call.arguments
                    )
                break
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise
                if attempt == request.limits.step_retry_count:
                    return ToolResult.failure(
                        summary="工具调用超时", error_code="tool_timeout"
                    ), "tool_timeout"
            except Exception:  # noqa: BLE001 - tool adapters are untrusted host boundaries
                if attempt == request.limits.step_retry_count:
                    return ToolResult.failure(
                        summary="工具调用失败", error_code="tool_failed"
                    ), "tool_failed"

        assert result is not None
        await self._publish_tool_completed(sink, request, call, result)
        if not result.ok:
            return result, result.error_code or "tool_failed"
        return result, None

    @staticmethod
    def _append_tool_result(
        messages: list[ModelMessage], call: ToolCall, result: ToolResult
    ) -> None:
        messages.append(
            ModelMessage(
                role="tool",
                name=call.name,
                tool_call_id=call.id,
                content=json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
            )
        )

    async def _publish_tool_completed(
        self, sink: EventSink, request: RunRequest, call: ToolCall, result: ToolResult
    ) -> None:
        await self._publish(
            sink,
            request,
            EventType.TOOL_COMPLETED,
            {
                "call_id": call.id,
                "tool": call.name,
                "ok": result.ok,
                "summary": result.summary,
                "error_code": result.error_code,
            },
        )

    @staticmethod
    def _ensure_before_deadline(deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError

    async def _finish(
        self,
        sink: EventSink,
        request: RunRequest,
        status: RunStatus,
        answer: str,
        tool_calls: int,
        error_code: str | None = None,
    ) -> RunResult:
        event_type = (
            EventType.RUN_COMPLETED
            if status is RunStatus.COMPLETED
            else EventType.RUN_FAILED
        )
        await self._publish(
            sink, request, event_type, {"status": status, "error_code": error_code}
        )
        return RunResult(
            run_id=request.run_id,
            status=status,
            answer=answer,
            tool_calls=tool_calls,
            error_code=error_code,
        )

    @staticmethod
    async def _publish(
        sink: EventSink,
        request: RunRequest,
        event_type: EventType,
        data: dict | None = None,
    ) -> None:
        await sink.publish(
            RuntimeEvent(type=event_type, run_id=request.run_id, data=data or {})
        )
