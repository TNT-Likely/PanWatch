"""Bounded, observable execution loop for a host-supplied model and tools."""

from __future__ import annotations

import asyncio
import json
import time

from .contracts import (
    EventType,
    ModelMessage,
    RunRequest,
    RunResult,
    RunStatus,
    RuntimeEvent,
    ToolCall,
    ToolResult,
)
from .errors import UnknownTool
from .ports import EventSink, ModelPort
from .registry import ToolRegistry


class AgentRuntime:
    """A small serial tool loop with hard resource limits.

    The host owns persistence and transport by implementing ``EventSink``;
    this class only emits portable events and never imports host concepts.
    """

    def __init__(self, model: ModelPort, tools: ToolRegistry) -> None:
        self._model = model
        self._tools = tools

    async def run(self, request: RunRequest, sink: EventSink) -> RunResult:
        await self._publish(sink, request, EventType.RUN_CREATED)
        messages = list(request.messages)
        deadline = time.monotonic() + request.limits.run_timeout_seconds
        tool_calls = 0
        answer = ""

        async def emit_token(token: str) -> None:
            nonlocal answer
            answer += token
            await self._publish(sink, request, EventType.ANSWER_TOKEN, {"token": token})

        try:
            for step_index in range(1, request.limits.max_steps + 1):
                self._ensure_before_deadline(deadline)
                await self._publish(sink, request, EventType.STEP_UPDATED, {"step": step_index, "status": "running"})
                turn = await self._run_model_turn(messages, emit_token, deadline)
                if turn.content and not answer:
                    await emit_token(turn.content)

                if not turn.tool_calls:
                    return await self._finish(sink, request, RunStatus.COMPLETED, answer, tool_calls)

                for call in turn.tool_calls:
                    if tool_calls >= request.limits.max_tool_calls:
                        return await self._finish(
                            sink, request, RunStatus.PARTIAL, answer, tool_calls, "tool_call_limit"
                        )
                    tool_calls += 1
                    result, error_code = await self._execute_call(request, sink, call)
                    if error_code:
                        return await self._finish(sink, request, RunStatus.PARTIAL, answer, tool_calls, error_code)
                    messages.append(ModelMessage(role="assistant", content="", name=call.name))
                    messages.append(
                        ModelMessage(
                            role="tool",
                            name=call.name,
                            tool_call_id=call.id,
                            content=json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                        )
                    )
            return await self._finish(sink, request, RunStatus.PARTIAL, answer, tool_calls, "step_limit")
        except TimeoutError:
            return await self._finish(sink, request, RunStatus.PARTIAL, answer, tool_calls, "run_timeout")
        except asyncio.CancelledError:
            return await self._finish(sink, request, RunStatus.CANCELLED, answer, tool_calls, "cancelled")
        except Exception:
            return await self._finish(sink, request, RunStatus.FAILED, answer, tool_calls, "runtime_failed")

    async def _run_model_turn(self, messages, emit_token, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            return await self._model.run_turn(messages, self._tools.model_tools(), emit_token)

    async def _execute_call(self, request: RunRequest, sink: EventSink, call: ToolCall) -> tuple[ToolResult, str | None]:
        try:
            self._tools.get(call.name)
        except UnknownTool:
            return ToolResult.failure(summary="请求的工具不可用", error_code="unknown_tool"), "unknown_tool"

        await self._publish(sink, request, EventType.TOOL_STARTED, {"call_id": call.id, "tool": call.name})
        result: ToolResult | None = None
        timeout = request.limits.tool_timeout_seconds
        for attempt in range(request.limits.step_retry_count + 1):
            try:
                async with asyncio.timeout(timeout):
                    result = await self._tools.execute(call.name, request, call.arguments)
                break
            except TimeoutError:
                if attempt == request.limits.step_retry_count:
                    return ToolResult.failure(summary="工具调用超时", error_code="tool_timeout"), "tool_timeout"
            except Exception:
                if attempt == request.limits.step_retry_count:
                    return ToolResult.failure(summary="工具调用失败", error_code="tool_failed"), "tool_failed"

        assert result is not None
        await self._publish(
            sink,
            request,
            EventType.TOOL_COMPLETED,
            {"call_id": call.id, "tool": call.name, "ok": result.ok, "summary": result.summary},
        )
        if not result.ok:
            return result, result.error_code or "tool_failed"
        return result, None

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
        event_type = EventType.RUN_COMPLETED if status is RunStatus.COMPLETED else EventType.RUN_FAILED
        await self._publish(sink, request, event_type, {"status": status, "error_code": error_code})
        return RunResult(
            run_id=request.run_id,
            status=status,
            answer=answer,
            tool_calls=tool_calls,
            error_code=error_code,
        )

    @staticmethod
    async def _publish(sink: EventSink, request: RunRequest, event_type: EventType, data: dict | None = None) -> None:
        await sink.publish(RuntimeEvent(type=event_type, run_id=request.run_id, data=data or {}))

