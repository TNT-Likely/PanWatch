"""Assistant-facing context DTOs built on the reusable PanAgent contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pan_agent import (
    ContextCompressionMode,
    ContextSummary,
    ContextUsage,
)
from pydantic import BaseModel


class CompressContextCommand(BaseModel):
    mode: ContextCompressionMode = ContextCompressionMode.BALANCED


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
