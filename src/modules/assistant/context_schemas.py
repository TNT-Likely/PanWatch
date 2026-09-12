"""Assistant-facing context DTOs built on the reusable PanAgent contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pan_agent import (
    ContextCompressionMode,
    ContextSummary,
    ContextUsage,
)
from pydantic import BaseModel, Field, model_validator


class CompressContextCommand(BaseModel):
    mode: ContextCompressionMode = ContextCompressionMode.BALANCED


class AssistantModelOption(BaseModel):
    id: int
    name: str
    model: str
    service_name: str


class AssistantConfigUpdate(BaseModel):
    compression_model_id: int | None = Field(default=None, ge=1)
    compression_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=12_000, ge=256)
    soft_limit_tokens: int = Field(default=8_400, ge=128)
    hard_limit_tokens: int = Field(default=10_200, ge=256)
    keep_recent_messages: int = Field(default=8, ge=1, le=100)

    @model_validator(mode="after")
    def validate_thresholds(self) -> "AssistantConfigUpdate":
        if not self.soft_limit_tokens < self.hard_limit_tokens <= self.max_tokens:
            raise ValueError(
                "context thresholds must satisfy soft_limit < hard_limit <= max_tokens"
            )
        return self


class AssistantConfigDTO(AssistantConfigUpdate):
    models: list[AssistantModelOption] = Field(default_factory=list)


class ContextSnapshotDTO(BaseModel):
    version: int
    mode: ContextCompressionMode
    summary: ContextSummary
    covered_until_message_id: int | None = None
    source_message_count: int
    usage_before: ContextUsage
    usage_after: ContextUsage
    created_at: datetime | None = None


class ContextDetailDTO(BaseModel):
    conversation_id: int
    usage: ContextUsage
    snapshot: ContextSnapshotDTO | None = None
    compression_available: bool = True
    status: Literal["normal", "warning", "needs_compression"]
