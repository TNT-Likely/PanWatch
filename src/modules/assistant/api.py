"""HTTP boundary for the navigation-level assistant.

The legacy ``/api/chat`` streaming endpoint remains available while clients
move to this module-owned surface.  Conversation state is already served here
so new integrations do not need to import or call router internals.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pan_agent import EventType, ModelMessage, RunLimits, RunRequest

from src.platform.persistence.database import get_db

from .repository import AssistantRepository
from .schemas import ConversationDetailDTO, ConversationDTO, CreateConversationCommand
from .service import AssistantNotFoundError, AssistantService


router = APIRouter()
logger = logging.getLogger(__name__)

# The runtime has its own deadline.  The HTTP boundary keeps the same bound so
# a misbehaving adapter cannot leave a browser request and durable task open.
ASSISTANT_RUN_TIMEOUT_SECONDS = 45
ASSISTANT_TOOL_TIMEOUT_SECONDS = 15

_ERROR_MESSAGES = {
    "run_timeout": "助手响应超时，请稍后重试。",
    "runtime_failed": "助手暂时不可用，请稍后重试。",
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


def _finish_failed_task(service: AssistantService, task_id: int, error_code: str) -> None:
    """Best-effort task cleanup shared by setup and streaming failure paths."""
    try:
        service.fail_task(task_id, error_code)
    except Exception:  # noqa: BLE001 - a client still needs a terminal event
        logger.exception("Assistant task state could not be persisted: task_id=%s", task_id)


def _error_response(error_code: str) -> StreamingResponse:
    async def events():
        yield _encode_sse("error", {"message": _error_message(error_code), "code": error_code})

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)


class SendAssistantMessageCommand(BaseModel):
    content: str


class _SSEEventSink:
    def __init__(self, queue: asyncio.Queue, service: AssistantService, task_id: int) -> None:
        self._queue, self._service, self._task_id = queue, service, task_id

    async def publish(self, event) -> None:
        data = dict(event.data)
        if event.type is EventType.RUN_CREATED:
            await self._queue.put(("status", {"message": "正在准备回答…"}))
        elif event.type is EventType.STEP_UPDATED:
            await self._queue.put(("status", {"message": "正在生成回答…"}))
        elif event.type is EventType.ANSWER_TOKEN:
            await self._queue.put(("token", {"text": data.get("token", "")}))
        elif event.type is EventType.TOOL_STARTED:
            await self._queue.put(("tool_call_start", {"name": data.get("tool", ""), "arguments": {}}))
        elif event.type is EventType.TOOL_COMPLETED:
            self._service.record_tool_completion(self._task_id, data)
            await self._queue.put(("tool_result", {"name": data.get("tool", ""), "ok": data.get("ok", False), "preview": data.get("summary", "")}))


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
        messages = [
            ModelMessage(role=item.role, content=item.content)
            for item in service.get_conversation(conversation_id).messages
        ]
        request = RunRequest(
            run_id=str(task.id),
            messages=messages,
            limits=RunLimits(
                run_timeout_seconds=ASSISTANT_RUN_TIMEOUT_SECONDS,
                tool_timeout_seconds=ASSISTANT_TOOL_TIMEOUT_SECONDS,
            ),
        )
    except AssistantNotFoundError as exc:
        if task is not None:
            _finish_failed_task(service, task.id, "transport_setup_failed")
            return _error_response("transport_setup_failed")
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001 - task setup failures must become terminal states
        if task is None:
            raise
        logger.exception(
            "Assistant task setup failed: task_id=%s conversation_id=%s",
            task.id,
            conversation_id,
        )
        _finish_failed_task(service, task.id, "transport_setup_failed")
        return _error_response("transport_setup_failed")

    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def fail(error_code: str, *, exc_info: bool = False) -> None:
        """Persist and publish a terminal failure without an empty chat message."""
        logger.error(
            "Assistant task failed: task_id=%s conversation_id=%s error_code=%s",
            task.id,
            conversation_id,
            error_code,
            exc_info=exc_info,
        )
        _finish_failed_task(service, task.id, error_code)
        await queue.put(("error", {"message": _error_message(error_code), "code": error_code}))

    async def run_runtime_with_timeout():
        """Keep the transport deadline authoritative even if a model adapter ignores cancellation."""
        runtime_task = asyncio.create_task(runtime.run(request, _SSEEventSink(queue, service, task.id)))

        def consume_background_result(completed_task: asyncio.Task) -> None:
            """Avoid an unobserved exception if a cancelled adapter finishes late."""
            try:
                completed_task.result()
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - the transport already sent a terminal event
                logger.exception("Assistant runtime stopped after transport cleanup: task_id=%s", task.id)

        try:
            return await asyncio.wait_for(
                asyncio.shield(runtime_task),
                timeout=ASSISTANT_RUN_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # Do not await cleanup here: an adapter can ignore cancellation.
            # The transport must still emit its terminal state at the deadline.
            if not runtime_task.done():
                runtime_task.add_done_callback(consume_background_result)
                runtime_task.cancel()
            raise

    async def produce() -> None:
        try:
            result = await run_runtime_with_timeout()
            if result.status.value != "completed" or not result.answer.strip():
                await fail(result.error_code or "empty_answer")
                return
            final = service.record_assistant_message(conversation_id, result.answer)
            service.finish_task(task.id, result, final.id)
            await queue.put(("done", {"message_id": final.id, "content": final.content, "created_at": final.created_at.isoformat() if final.created_at else ""}))
        except asyncio.TimeoutError:
            await fail("transport_timeout")
        except asyncio.CancelledError:
            # A disconnected browser must not leave a durable task permanently running.
            await fail("cancelled")
            raise
        except Exception:  # noqa: BLE001 - provider details stay in server logs
            await fail("transport_failed", exc_info=True)
        finally:
            await queue.put(None)

    # Start before returning the response: even an immediately disconnected
    # client cannot strand a durable task in ``running`` without a worker.
    worker = asyncio.create_task(produce())

    async def events():
        try:
            # Flush a first event before waiting on an unpredictable model/provider.
            yield _encode_sse("status", {"message": "正在理解你的问题…"})
            while (item := await queue.get()) is not None:
                event, data = item
                yield _encode_sse(event, data)
        finally:
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "runtime": "pan-agent-runtime"}


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
