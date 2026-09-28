"""Versioned contracts for evidence-backed assistant results."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AssistantEvidence(BaseModel):
    id: str
    tool_name: str
    source_name: str
    source_url: str | None = None
    summary: str
    observed_at: datetime | None = None
    data_at: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    freshness: Literal["fresh", "delayed", "stale", "unknown"] = "unknown"
    freshness_basis: Literal["published_at", "as_of", "observed_at", "unknown"] = "unknown"
    symbol: str | None = None
    market: str | None = None


class AssistantFact(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class AssistantNextAction(BaseModel):
    id: str
    kind: Literal["follow_up", "navigate", "tool_proposal"]
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False


class AssistantResult(BaseModel):
    schema_version: int = 1
    summary: str = ""
    facts: list[AssistantFact] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    evidence: list[AssistantEvidence] = Field(default_factory=list)
    next_actions: list[AssistantNextAction] = Field(default_factory=list)
