"""Public DTOs for the interactive assistant module."""

from __future__ import annotations

from datetime import datetime

from pan_agent import ApprovalDecision
from pydantic import BaseModel, Field


class CreateConversationCommand(BaseModel):
    stock_symbol: str | None = Field(default=None, max_length=32)
    stock_market: str | None = Field(default=None, max_length=16)
    initial_context: str | None = Field(default=None, max_length=20_000)


class ApprovalDecisionCommand(BaseModel):
    decision: ApprovalDecision


class ConversationDTO(BaseModel):
    id: int
    title: str = ""
    stock_symbol: str | None = None
    stock_market: str | None = None
    created_at: datetime | None = None


class MessageDTO(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime | None = None


class ConversationDetailDTO(BaseModel):
    conversation: ConversationDTO
    messages: list[MessageDTO]
