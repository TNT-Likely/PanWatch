"""Pydantic contracts shared by hosts and the PanAgent runtime."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ToolRisk(StrEnum):
    """The capability class used by the runtime safety gate."""

    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"
    DESTRUCTIVE = "destructive"


class PermissionMode(StrEnum):
    """The host-owned outcome for one proposed tool call."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ApprovalDecision(StrEnum):
    """A durable human decision for a pending tool call."""

    APPROVED = "approved"
    REJECTED = "rejected"


class ToolPermissionDecision(BaseModel):
    """A policy decision that the model cannot create or override."""

    mode: PermissionMode
    reason: str = ""

    @classmethod
    def allow(cls) -> ToolPermissionDecision:
        return cls(mode=PermissionMode.ALLOW)

    @classmethod
    def ask(cls, reason: str = "") -> ToolPermissionDecision:
        return cls(mode=PermissionMode.ASK, reason=reason)

    @classmethod
    def deny(cls, reason: str) -> ToolPermissionDecision:
        return cls(mode=PermissionMode.DENY, reason=reason)


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_FOR_APPROVAL = "waiting_for_approval"


class EventType(StrEnum):
    RUN_CREATED = "run_created"
    PLAN_CREATED = "plan_created"
    STEP_UPDATED = "step_updated"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    ANSWER_TOKEN = "answer_token"
    APPROVAL_REQUIRED = "approval_required"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class Source(BaseModel):
    name: str = Field(min_length=1)
    url: str | None = None


class ToolSpec(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2_000)
    risk: ToolRisk = ToolRisk.READ
    confirmation_required: bool = False
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})

    @field_validator("input_schema")
    @classmethod
    def validate_input_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("input_schema must describe an object")
        return value

    def openai_schema(self) -> dict[str, Any]:
        """Expose the provider-neutral tool definition in OpenAI-compatible form."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolResult(BaseModel):
    ok: bool
    summary: str = Field(min_length=1, max_length=20_000)
    data: dict[str, Any] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list)
    observed_at: datetime | None = None
    error_code: str | None = None

    @classmethod
    def success(
        cls,
        *,
        summary: str,
        data: dict[str, Any],
        sources: list[Source | dict[str, Any]],
        observed_at: datetime,
    ) -> ToolResult:
        return cls(
            ok=True,
            summary=summary,
            data=data,
            sources=sources,
            observed_at=observed_at,
        )

    @classmethod
    def failure(cls, *, summary: str, error_code: str = "tool_failed") -> ToolResult:
        return cls(ok=False, summary=summary, error_code=error_code)


class RunLimits(BaseModel):
    max_steps: int = Field(default=6, ge=1, le=32)
    max_tool_calls: int = Field(default=8, ge=1, le=64)
    tool_timeout_seconds: int = Field(default=20, ge=1, le=120)
    run_timeout_seconds: int = Field(default=90, ge=1, le=600)
    step_retry_count: int = Field(default=1, ge=0, le=3)


class ToolCall(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelMessage(BaseModel):
    """A provider-neutral conversation message passed between model turns.

    Tool results must retain the assistant call that produced them.  The host
    adapter converts ``tool_calls`` to the chosen provider's wire format.
    """

    role: str = Field(pattern=r"^(system|user|assistant|tool)$")
    content: str = ""
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class PendingApproval(BaseModel):
    """A proposed call preserved until a trusted human decides it."""

    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    risk: ToolRisk
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentCheckpoint(BaseModel):
    """Provider-neutral state required to resume a paused agent run."""

    messages: list[ModelMessage]
    answer: str = ""
    step_index: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    pending_approvals: list[PendingApproval] = Field(default_factory=list)


class ModelTurn(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None


class RunRequest(BaseModel):
    run_id: str = Field(min_length=1)
    messages: list[ModelMessage] = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    limits: RunLimits = Field(default_factory=RunLimits)


class RuntimeEvent(BaseModel):
    type: EventType
    run_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=datetime.now)


class RunResult(BaseModel):
    run_id: str
    status: RunStatus
    answer: str = ""
    tool_calls: int = 0
    error_code: str | None = None
    checkpoint: AgentCheckpoint | None = None
    pending_approvals: list[PendingApproval] = Field(default_factory=list)
