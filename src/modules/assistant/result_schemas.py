"""Versioned contracts for evidence-backed assistant results."""

from __future__ import annotations

from typing import Any, Literal

from src.web.datetime import UTCDateTime

from pydantic import BaseModel, Field, model_validator


SOURCE_TIME_WARNINGS = {
    "zh-CN": "部分依据缺少可验证的数据时点或已经过期，请结合最新数据复核。",
    "en-US": "Some evidence has an unknown or stale data time; verify it against current data.",
}
LOCAL_SNAPSHOT_TOOLS = {
    "check_watch_request", "search_stocks", "get_market_status", "get_watchlist",
    "get_notification_channels", "get_price_alerts", "get_monitoring_health",
    "create_price_alert", "update_price_alert", "delete_price_alert",
}


def evidence_kind(tool_name: str) -> Literal["source_data", "tool_discovery", "local_snapshot"]:
    # Only these contracts establish execution metadata or a local DB snapshot.
    # Never infer a market/news source timestamp from its retrieval time.
    if tool_name == "tool_search":
        return "tool_discovery"
    if tool_name in LOCAL_SNAPSHOT_TOOLS:
        return "local_snapshot"
    return "source_data"


class AssistantEvidence(BaseModel):
    id: str
    tool_name: str
    source_name: str
    source_url: str | None = None
    summary: str
    evidence_kind: Literal["source_data", "tool_discovery", "local_snapshot"] = "source_data"
    observed_at: UTCDateTime | None = None
    data_at: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    freshness: Literal["fresh", "delayed", "stale", "unknown"] = "unknown"
    freshness_basis: Literal["published_at", "as_of", "observed_at", "source_timestamp", "quote_date", "unknown"] = "unknown"
    market_status: str | None = None
    quote_date: str | None = None
    symbol: str | None = None
    market: str | None = None

    @model_validator(mode="after")
    def classify_time_semantics(self):
        self.evidence_kind = evidence_kind(self.tool_name)
        # Legacy rule reads retained observed_at, but had no separate as_of.
        # This is the historical query snapshot, never the time of this read.
        if self.evidence_kind == "local_snapshot" and not self.data_at and self.observed_at:
            self.data_at = self.observed_at.isoformat()
            self.freshness_basis = "observed_at"
        return self

    @property
    def needs_source_time_warning(self) -> bool:
        return self.evidence_kind == "source_data" and self.freshness in {"stale", "unknown"}


class AssistantFact(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class AssistantJudgment(BaseModel):
    step_id: str
    title: str
    text: str
    status: str = "completed"
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
    judgments: list[AssistantJudgment] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    evidence: list[AssistantEvidence] = Field(default_factory=list)
    next_actions: list[AssistantNextAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def restore_time_warnings(self):
        # Correct only our exact generated warning in stored results. Preserve
        # user/model risks and any warning supported by market/news evidence.
        if self.evidence and not any(item.needs_source_time_warning for item in self.evidence):
            self.risks = [item for item in self.risks if item not in SOURCE_TIME_WARNINGS.values()]
        return self


def restored_result_payload(value: dict | None) -> dict | None:
    """Normalize a persisted response without rewriting its historical record."""
    if not isinstance(value, dict) or "evidence" not in value:
        return value
    return AssistantResult.model_validate(value).model_dump(mode="json")
