"""Public DTOs for the interactive assistant module."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pan_agent import ApprovalDecision, PermissionMode, ToolRisk
from pydantic import BaseModel, Field, PositiveInt, field_validator

from .result_schemas import AssistantResult


class CreateConversationCommand(BaseModel):
    stock_symbol: str | None = Field(default=None, max_length=32)
    stock_market: str | None = Field(default=None, max_length=16)
    initial_context: str | None = Field(default=None, max_length=20_000)


class RenameConversationCommand(BaseModel):
    title: str = Field(min_length=1, max_length=80)

    @field_validator('title')
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = ' '.join(value.split())
        if not value:
            raise ValueError('Title cannot be blank')
        return value


class ApprovalDecisionCommand(BaseModel):
    decision: ApprovalDecision


class ToolPermissionCommand(BaseModel):
    selector_kind: Literal["tool", "risk"]
    selector_value: str = Field(min_length=1, max_length=64)
    mode: PermissionMode
    risk: ToolRisk | None = None


class ConversationDTO(BaseModel):
    id: int
    title: str = ""
    title_source: str = "provisional"
    stock_symbol: str | None = None
    stock_market: str | None = None
    created_at: datetime | None = None


class MessageDTO(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime | None = None
    result: AssistantResult | None = None
    trace: list[dict[str, Any]] | None = None


class ConversationDetailDTO(BaseModel):
    conversation: ConversationDTO
    messages: list[MessageDTO]
    latest_task: dict[str, Any] | None = None


class AssistantActivityTaskDTO(BaseModel):
    id: int
    conversation_id: int
    title: str
    status: str
    current_step: int
    started_at: datetime | None = None
    created_at: datetime | None = None


class AssistantNotificationDTO(BaseModel):
    id: int
    task_id: int
    conversation_id: int
    title: str
    kind: Literal["completed", "failed", "awaiting_approval"]
    created_at: datetime | None = None
    read_at: datetime | None = None


class AssistantActivityDTO(BaseModel):
    active_tasks: list[AssistantActivityTaskDTO]
    notifications: list[AssistantNotificationDTO]
    unread_count: int
    notification_cursor: int


class ReadAssistantNotificationsCommand(BaseModel):
    ids: list[PositiveInt] = Field(default_factory=list, max_length=100)
    through_id: PositiveInt | None = None
