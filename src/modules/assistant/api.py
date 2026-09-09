"""HTTP boundary for the navigation-level assistant.

The legacy ``/api/chat`` streaming endpoint remains available while clients
move to this module-owned surface.  Conversation state is already served here
so new integrations do not need to import or call router internals.
"""

from __future__ import annotations

import asyncio
import json
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


class SendAssistantMessageCommand(BaseModel):
    content: str


class _SSEEventSink:
    def __init__(self, queue: asyncio.Queue, service: AssistantService, task_id: int) -> None:
        self._queue, self._service, self._task_id = queue, service, task_id

    async def publish(self, event) -> None:
        data = dict(event.data)
        if event.type is EventType.ANSWER_TOKEN:
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
    try:
        user_message = service.record_user_message(conversation_id, body.content)
        task = service.create_task(conversation_id, user_message.id)
        runtime = service.build_runtime(service.build_failover_client())
    except AssistantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    messages = [ModelMessage(role=item.role, content=item.content) for item in service.get_conversation(conversation_id).messages]
    request = RunRequest(run_id=str(task.id), messages=messages, limits=RunLimits())
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def produce() -> None:
        try:
            result = await runtime.run(request, _SSEEventSink(queue, service, task.id))
            final = service.record_assistant_message(conversation_id, result.answer)
            service.finish_task(task.id, result, final.id)
            await queue.put(("done", {"message_id": final.id, "content": final.content, "created_at": final.created_at.isoformat() if final.created_at else ""}))
        except Exception:
            await queue.put(("error", {"message": "助手任务执行失败"}))
        finally:
            await queue.put(None)

    async def events():
        worker = asyncio.create_task(produce())
        try:
            while (item := await queue.get()) is not None:
                event, data = item
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        finally:
            if not worker.done():
                worker.cancel()

    return StreamingResponse(events(), media_type="text/event-stream")


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
