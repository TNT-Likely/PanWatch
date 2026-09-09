"""HTTP boundary for the navigation-level assistant.

The legacy ``/api/chat`` streaming endpoint remains available while clients
move to this module-owned surface.  Conversation state is already served here
so new integrations do not need to import or call router internals.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.platform.persistence.database import get_db

from .repository import AssistantRepository
from .schemas import ConversationDetailDTO, ConversationDTO, CreateConversationCommand
from .service import AssistantNotFoundError, AssistantService


router = APIRouter()


def get_assistant_service(db: Session = Depends(get_db)) -> AssistantService:
    return AssistantService(AssistantRepository(db))


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
