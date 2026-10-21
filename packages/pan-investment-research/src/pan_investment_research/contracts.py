"""Serializable research inputs and artifacts, independent of any application."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Role = Literal["gate", "execution", "technical", "flow", "events", "bull", "bear", "manager", "strategy", "risk", "decision"]
Action = Literal["buy", "add", "reduce", "sell", "hold", "watch", "avoid"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Contract):
    id: str = Field(min_length=1)
    stock_key: str
    domain: Literal["technical", "flow", "events", "portfolio", "market"]
    source: str = Field(min_length=1)
    observed_at: AwareDatetime
    available_at: AwareDatetime
    content: dict[str, Any]
    url: str = ""


class Security(Contract):
    key: str = Field(pattern=r"^[A-Z]+:.+$")
    name: str = ""
    price: FiniteFloat | None = Field(default=None, gt=0)
    quote_expires_at: AwareDatetime | None = None
    held_quantity: FiniteFloat | None = Field(default=None, ge=0)
    sellable_quantity: FiniteFloat | None = Field(default=None, ge=0)


class ResearchSignal(Contract):
    stock_key: str
    domain: Literal["technical", "events"]
    reasons: list[str] = Field(min_length=1)
    observed_at: AwareDatetime
    material: bool = False


class ResearchInput(Contract):
    run_id: str = Field(min_length=1)
    as_of: AwareDatetime
    securities: list[Security] = Field(min_length=1)
    evidence: list[Evidence]
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    portfolio: dict[str, Any] = Field(default_factory=dict)
    signals: list[ResearchSignal] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self):
        keys = [s.key for s in self.securities]
        ids = [e.id for e in self.evidence]
        if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
            raise ValueError("security keys and evidence ids must be unique")
        if any(e.stock_key not in keys and e.stock_key != "*" for e in self.evidence):
            raise ValueError("evidence outside research scope")
        if any(e.available_at > self.as_of for e in self.evidence):
            raise ValueError("evidence was unavailable at the analysis cutoff")
        if any(s.stock_key not in keys or s.observed_at > self.as_of for s in self.signals):
            raise ValueError("signal outside research scope or analysis cutoff")
        return self


class Claim(Contract):
    id: str
    stock_key: str
    statement: str
    evidence_ids: list[str] = Field(min_length=1)
    inference: bool = True
    invalidations: list[str] = Field(default_factory=list)


class Issue(Contract):
    stock_key: str
    domain: Literal["technical", "flow", "events"]
    question: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    respondent: Literal["bull", "bear"] | None = None


class Recommendation(Contract):
    stock_key: str
    outlook: Literal["bullish", "neutral", "bearish"]
    confidence: FiniteFloat = Field(ge=0, le=1)
    action: Action
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    invalidations: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    quantity: int | None = Field(default=None, gt=0)
    review_required: bool = False
    attention_required: bool = False
    execution_status: Literal["conditional", "blocked", "not_applicable"] = "conditional"
    execution_constraints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def review_is_not_a_trade(self):
        if (self.review_required or self.execution_status == "blocked") and self.quantity is not None:
            raise ValueError("review or blocked recommendations cannot authorize quantity")
        return self


class SessionNote(Contract):
    stock_key: str
    summary: str = Field(min_length=1)
    upside: str = ""
    downside: str = ""
    prior_verdict: Literal["confirmed", "refuted", "mixed", "unresolved", "no_prior"] = "no_prior"
    evidence_ids: list[str] = Field(min_length=1)


class RoleReport(Contract):
    summary: str = Field(min_length=1)
    claims: list[Claim] = Field(default_factory=list)
    accepted_claim_ids: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    verdict: Literal["ready", "pass", "revise", "evidence_needed", "rebuttal", "reject"] = "ready"
    debate_requested: bool = False
    session_notes: list[SessionNote] = Field(default_factory=list)


class Artifact(Contract):
    id: str
    role: Role
    epoch: int
    report: RoleReport
    evidence: list[Evidence] = Field(default_factory=list)
    model: str = ""
    kind: Literal["model", "rule"] = "model"


class ResearchResult(Contract):
    run_id: str
    status: Literal["ready", "review", "skipped"]
    completion: Literal["complete", "skipped", "timeout", "budget_exhausted", "validation_failed", "risk_rejected"] = "complete"
    graph: Literal["research", "intraday_fast"] = "research"
    recommendations: list[Recommendation]
    artifacts: list[Artifact]
    reason: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)


class ResearchPolicy(Contract):
    profile: Literal["postmarket", "premarket", "intraday"] = "postmarket"
    graph: Literal["research", "intraday_fast"] = "research"
    specialists: list[Literal["technical", "flow", "events"]] = Field(default_factory=lambda: ["technical", "flow", "events"], min_length=1)
    trigger_mode: Literal["changes", "always"] = "changes"
    debate: bool = True
    debate_on: Literal["always", "material"] = "always"
    max_feedback: int = Field(default=1, ge=0, le=2)
    max_attempts: int = Field(default=18, ge=1, le=100)
    max_tools: int = Field(default=6, ge=0, le=30)
    max_tool_rounds: int = Field(default=1, ge=0, le=2)
    concurrency: int = Field(default=3, ge=1, le=6)
    deadline_seconds: float = Field(default=180, gt=0, le=600)
    max_output_tokens: int = Field(default=4096, ge=128, le=8000)
    max_input_chars: int = Field(default=60000, ge=1000, le=250000)
    max_total_tokens: int = Field(default=250000, ge=1000, le=1000000)
    max_group_size: int = Field(default=8, ge=1, le=30)

    @model_validator(mode="after")
    def unique_roles(self):
        if len(self.specialists) != len(set(self.specialists)):
            raise ValueError("duplicate specialist")
        if self.graph == "intraday_fast":
            if self.profile != "intraday" or set(self.specialists) - {"technical", "events"}:
                raise ValueError("intraday_fast supports intraday technical/event specialists only")
            if self.debate or self.max_feedback:
                raise ValueError("intraday_fast does not run debate or feedback")
        return self
