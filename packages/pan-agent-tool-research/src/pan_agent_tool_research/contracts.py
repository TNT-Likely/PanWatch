"""Provider-neutral contracts for discovering executable tools."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from pan_agent import ToolRisk


class ToolDataFreshness(StrEnum):
    STATIC = "static"
    NEAR_REAL_TIME = "near_real_time"
    REAL_TIME = "real_time"


class ToolDescriptor(BaseModel):
    """Search metadata kept separate from the model-facing ToolSpec schema."""

    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    descriptor_version: str = Field(default="1", min_length=1)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    use_cases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    input_summary: str = ""
    output_summary: str = ""
    domain: str = Field(default="general", min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    data_freshness: ToolDataFreshness = ToolDataFreshness.STATIC
    estimated_latency_ms: int | None = Field(default=None, ge=0)
    estimated_cost: str | None = None
    side_effects: list[str] = Field(default_factory=list)
    risk: ToolRisk = ToolRisk.READ
    confirmation_required: bool = False
    requires: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    visibility_scope: list[str] = Field(default_factory=list)
    implementation_version: str = Field(default="1", min_length=1)
    enabled: bool = True


class ToolResearchRequest(BaseModel):
    query: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    allowed_domains: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    excluded_tools: list[str] = Field(default_factory=list)
    permission_scope: str | None = None
    mode: Literal["fast", "balanced", "deep"] = "balanced"
    max_candidates: int = Field(default=32, ge=1, le=128)
    max_selected: int = Field(default=8, ge=1, le=32)
    include_write_tools: bool = False
    registry_version: str | None = None


class ToolCandidate(BaseModel):
    tool_name: str
    score: float = Field(ge=0, le=1)
    match_reasons: list[str] = Field(default_factory=list)
    matched_fields: list[str] = Field(default_factory=list)
    domain: str = "general"
    filtered: bool = False
    filter_reasons: list[str] = Field(default_factory=list)


class ToolSelectionPolicy(BaseModel):
    max_selected: int = Field(default=8, ge=1, le=32)
    max_candidates: int = Field(default=32, ge=1, le=128)
    max_per_domain: int = Field(default=4, ge=1, le=32)
    max_write_tools: int = Field(default=0, ge=0, le=32)
    min_score: float = Field(default=0.20, ge=0, le=1)
    always_include: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)


class ToolResearchResult(BaseModel):
    candidates: list[ToolCandidate] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    filters_applied: list[str] = Field(default_factory=list)
    fallback: bool = False
    fallback_reason: str | None = None
    registry_version: str
    catalog_version: str
    latency_ms: int = Field(ge=0)
