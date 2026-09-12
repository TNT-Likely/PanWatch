"""HTTP boundary for the navigation-level assistant.

The legacy ``/api/chat`` streaming endpoint remains available while clients
move to this module-owned surface.  Conversation state is already served here
so new integrations do not need to import or call router internals.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pan_agent import (
    EventType,
    ModelMessage,
    RunLimits,
    RunRequest,
    RunResult,
    RunStatus,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.platform.persistence.database import get_db

from .grounding import UNVERIFIED_MUTATION_ERROR, unverified_mutation_claim
from .prompt import build_assistant_messages
from .repository import AssistantRepository
from .schemas import (
    ApprovalDecisionCommand,
    ConversationDetailDTO,
    ConversationDTO,
    CreateConversationCommand,
    ToolPermissionCommand,
)
from .service import (
    AssistantApprovalConflictError,
    AssistantApprovalExpiredError,
    AssistantNotFoundError,
    AssistantService,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# The runtime has its own deadline.  The HTTP boundary keeps the same bound so
# a misbehaving adapter cannot leave a browser request and durable task open.
ASSISTANT_RUN_TIMEOUT_SECONDS = 45
ASSISTANT_TOOL_TIMEOUT_SECONDS = 15
# 研究型请求可能需要行情、K 线、新闻和持仓多轮组合调用；同时由
# PanAgent runtime 的重复调用保护避免小模型陷入同一工具循环。
ASSISTANT_MAX_STEPS = 12
ASSISTANT_MAX_TOOL_CALLS = 24

_ERROR_MESSAGES = {
    "run_timeout": "助手响应超时，请稍后重试。",
    "tool_call_limit": "助手调用步骤过多，请缩小问题范围后重试。",
    "repeated_tool_call": "助手检测到重复工具调用，请重试或换一种问法。",
    "runtime_failed": "助手暂时不可用，请稍后重试。",
    UNVERIFIED_MUTATION_ERROR: "助手没有执行写入操作，请重新确认后再试。",
    "transport_timeout": "助手响应超时，请稍后重试。",
    "transport_failed": "助手任务执行失败，请稍后重试。",
    "transport_setup_failed": "助手任务执行失败，请稍后重试。",
}
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # Disable reverse-proxy buffering so status and token events are flushed.
    "X-Accel-Buffering": "no",
}


def _error_message(error_code: str) -> str:
    return _ERROR_MESSAGES.get(error_code, "助手暂时不可用，请稍后重试。")


def _encode_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _finish_failed_task(
    service: AssistantService, task_id: int, error_code: str
) -> None:
    """Best-effort task cleanup shared by setup and streaming failure paths."""
    try:
        service.fail_task(task_id, error_code)
    except Exception:  # a client still needs a terminal event
        logger.exception(
            "Assistant task state could not be persisted: task_id=%s", task_id
        )


def _error_response(error_code: str) -> StreamingResponse:
    async def events():
        yield _encode_sse(
            "error", {"message": _error_message(error_code), "code": error_code}
        )

    return StreamingResponse(
        events(), media_type="text/event-stream", headers=_SSE_HEADERS
    )


class SendAssistantMessageCommand(BaseModel):
    content: str


class _SSEEventSink:
    def __init__(
        self,
        queue: asyncio.Queue,
        service: AssistantService,
        task_id: int,
        completed_tools: list[dict] | None = None,
    ) -> None:
        self._queue, self._service, self._task_id = queue, service, task_id
        self._completed_tools = completed_tools

    async def publish(self, event) -> None:
        data = dict(event.data)
        if event.type is EventType.RUN_CREATED:
            await self._queue.put(("run_started", {"task_id": self._task_id}))
        elif event.type is EventType.ANSWER_TOKEN:
            await self._queue.put(("token", {"text": data.get("token", "")}))
        elif event.type is EventType.TOOL_STARTED:
            await self._queue.put(
                ("tool_call_start", {"name": data.get("tool", ""), "arguments": {}})
            )
        elif event.type is EventType.TOOL_COMPLETED:
            if self._completed_tools is not None:
                self._completed_tools.append(
                    {
                        "name": data.get("tool", ""),
                        "ok": bool(data.get("ok")),
                    }
                )
            self._service.record_tool_completion(self._task_id, data)
            await self._queue.put(
                (
                    "tool_result",
                    {
                        "name": data.get("tool", ""),
                        "ok": data.get("ok", False),
                        "preview": data.get("summary", ""),
                    },
                )
            )
        elif event.type is EventType.APPROVAL_REQUIRED:
            # Browser-facing approval IDs exist only after the host persists
            # the checkpoint. The stream worker emits the host-facing event.
            return


def _approval_event_payload(approval) -> dict:
    expires_at = approval.expires_at
    return {
        "approval_id": approval.id,
        "presentation": approval.presentation or {},
        "calls": [
            {
                "call_id": approval.call_id,
                "name": approval.tool_name,
                "risk": approval.risk,
                "arguments": approval.arguments or {},
            }
        ],
        "expires_at": expires_at.isoformat() if expires_at else "",
    }


async def _stream_runtime(
    *,
    task_id: int,
    conversation_id: int,
    service: AssistantService,
    runtime_call: Callable[[_SSEEventSink], Awaitable[RunResult]],
    paused_extra: dict | None = None,
) -> StreamingResponse:
    """Run or resume one task while keeping HTTP transport out of the runtime."""
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
    completed_tools: list[dict] = []
    mutation_tool_names = (
        set(service.mutation_tool_names())
        if callable(getattr(service, "mutation_tool_names", None))
        else set()
    )

    async def fail(error_code: str, *, exc_info: bool = False) -> None:
        logger.error(
            "Assistant task failed: task_id=%s conversation_id=%s error_code=%s",
            task_id,
            conversation_id,
            error_code,
            exc_info=exc_info,
        )
        _finish_failed_task(service, task_id, error_code)
        await queue.put(
            ("error", {"message": _error_message(error_code), "code": error_code})
        )

    async def run_runtime_with_timeout() -> RunResult:
        runtime_task = asyncio.create_task(
            runtime_call(
                _SSEEventSink(queue, service, task_id, completed_tools)
            )
        )

        def consume_background_result(completed_task: asyncio.Task) -> None:
            try:
                completed_task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "Assistant runtime stopped after transport cleanup: task_id=%s",
                    task_id,
                )

        try:
            return await asyncio.wait_for(
                asyncio.shield(runtime_task),
                timeout=ASSISTANT_RUN_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if not runtime_task.done():
                runtime_task.add_done_callback(consume_background_result)
                runtime_task.cancel()
            raise

    async def produce() -> None:
        try:
            result = await run_runtime_with_timeout()
            if result.status is RunStatus.WAITING_FOR_APPROVAL:
                approvals = service.pause_task(task_id, result)
                for approval in approvals:
                    await queue.put(
                        ("approval_required", _approval_event_payload(approval))
                    )
                paused_payload = {
                    "task_id": task_id,
                    "reason": "approval_required",
                }
                if paused_extra:
                    paused_payload.update(paused_extra)
                await queue.put(
                    ("paused", paused_payload)
                )
                return
            if result.status is not RunStatus.COMPLETED or not result.answer.strip():
                await fail(result.error_code or "empty_answer")
                return
            mutation_error = unverified_mutation_claim(
                result.answer,
                completed_tools,
                mutation_tool_names,
            )
            if mutation_error:
                await fail(UNVERIFIED_MUTATION_ERROR)
                return
            final = service.record_assistant_message(conversation_id, result.answer)
            service.finish_task(task_id, result, final.id)
            await queue.put(
                (
                    "done",
                    {
                        "message_id": final.id,
                        "content": final.content,
                        "created_at": final.created_at.isoformat()
                        if final.created_at
                        else "",
                    },
                )
            )
        except asyncio.TimeoutError:
            await fail("transport_timeout")
        except asyncio.CancelledError:
            await fail("cancelled")
            raise
        except Exception:  # noqa: BLE001 - provider details stay in server logs
            await fail("transport_failed", exc_info=True)
        finally:
            await queue.put(None)

    worker = asyncio.create_task(produce())

    async def events():
        try:
            while (item := await queue.get()) is not None:
                event, data = item
                yield _encode_sse(event, data)
        finally:
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    return StreamingResponse(
        events(), media_type="text/event-stream", headers=_SSE_HEADERS
    )


def get_assistant_service(db: Session = Depends(get_db)) -> AssistantService:
    return AssistantService(AssistantRepository(db))


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_assistant_message(
    conversation_id: int,
    body: SendAssistantMessageCommand,
    service: AssistantService = Depends(get_assistant_service),
):
    """Run the navigation assistant through PanAgent and stream its portable events."""
    task = None
    try:
        user_message = service.record_user_message(conversation_id, body.content)
        task = service.create_task(conversation_id, user_message.id)
        runtime = service.build_runtime(service.build_failover_client())
        messages = build_assistant_messages(
            [
                ModelMessage(role=item.role, content=item.content)
                for item in service.get_conversation(conversation_id).messages
            ]
        )
        request = RunRequest(
            run_id=str(task.id),
            messages=messages,
            limits=RunLimits(
                max_steps=ASSISTANT_MAX_STEPS,
                max_tool_calls=ASSISTANT_MAX_TOOL_CALLS,
                run_timeout_seconds=ASSISTANT_RUN_TIMEOUT_SECONDS,
                tool_timeout_seconds=ASSISTANT_TOOL_TIMEOUT_SECONDS,
            ),
        )
    except AssistantNotFoundError as exc:
        if task is not None:
            _finish_failed_task(service, task.id, "transport_setup_failed")
            return _error_response("transport_setup_failed")
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:  # task setup failures must become terminal states
        if task is None:
            raise
        logger.exception(
            "Assistant task setup failed: task_id=%s conversation_id=%s",
            task.id,
            conversation_id,
        )
        _finish_failed_task(service, task.id, "transport_setup_failed")
        return _error_response("transport_setup_failed")

    return await _stream_runtime(
        task_id=task.id,
        conversation_id=conversation_id,
        service=service,
        runtime_call=lambda sink: runtime.run(request, sink),
    )


@router.post("/approvals/{approval_id}/decision/stream")
async def stream_assistant_approval_decision(
    approval_id: str,
    body: ApprovalDecisionCommand,
    service: AssistantService = Depends(get_assistant_service),
):
    """Consume one approval card and resume its tool call immediately."""
    try:
        outcome = service.resolve_approval_decision(approval_id, body.decision)
    except AssistantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (AssistantApprovalConflictError, AssistantApprovalExpiredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if outcome.checkpoint is None:

        async def events():
            yield _encode_sse(
                "paused",
                {
                    "task_id": outcome.task.id,
                    "reason": "approval_required",
                    "resolved_approval_id": approval_id,
                    "resolved_status": body.decision.value,
                },
            )

        return StreamingResponse(
            events(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    try:
        runtime = service.build_runtime(service.build_failover_client())
        request = RunRequest(
            run_id=str(outcome.task.id),
            messages=outcome.checkpoint.messages,
            limits=RunLimits(
                max_steps=ASSISTANT_MAX_STEPS,
                max_tool_calls=ASSISTANT_MAX_TOOL_CALLS,
                run_timeout_seconds=ASSISTANT_RUN_TIMEOUT_SECONDS,
                tool_timeout_seconds=ASSISTANT_TOOL_TIMEOUT_SECONDS,
            ),
        )
    except Exception:
        logger.exception(
            "Assistant approval resume setup failed: approval_id=%s", approval_id
        )
        _finish_failed_task(service, outcome.task.id, "transport_setup_failed")
        return _error_response("transport_setup_failed")

    return await _stream_runtime(
        task_id=outcome.task.id,
        conversation_id=outcome.task.conversation_id,
        service=service,
        runtime_call=lambda sink: runtime.resume(
            request, outcome.checkpoint, outcome.decisions, sink
        ),
        paused_extra={
            "resolved_approval_id": approval_id,
            "resolved_status": body.decision.value,
        },
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "runtime": "pan-agent-runtime"}


@router.get("/tool-permissions")
def get_tool_permissions(
    service: AssistantService = Depends(get_assistant_service),
) -> dict:
    return service.get_tool_permissions()


@router.put("/tool-permissions")
def update_tool_permission(
    body: ToolPermissionCommand,
    service: AssistantService = Depends(get_assistant_service),
) -> dict:
    try:
        return service.update_tool_permission(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/conversations", response_model=ConversationDTO)
def create_conversation(
    body: CreateConversationCommand,
    service: AssistantService = Depends(get_assistant_service),
) -> ConversationDTO:
    return service.create_conversation(body)


@router.get("/conversations", response_model=list[ConversationDTO])
def list_conversations(
    limit: int = Query(30, ge=1, le=100),
    service: AssistantService = Depends(get_assistant_service),
) -> list[ConversationDTO]:
    return service.list_conversations(limit)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailDTO)
def get_conversation(
    conversation_id: int,
    service: AssistantService = Depends(get_assistant_service),
) -> ConversationDetailDTO:
    try:
        return service.get_conversation(conversation_id)
    except AssistantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    service: AssistantService = Depends(get_assistant_service),
) -> dict[str, bool]:
    try:
        service.delete_conversation(conversation_id)
    except AssistantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/tasks/{task_run_id}")
def get_task_snapshot(
    task_run_id: int,
    service: AssistantService = Depends(get_assistant_service),
) -> dict:
    try:
        return service.get_task_snapshot(task_run_id)
    except AssistantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
