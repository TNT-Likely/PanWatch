"""Provider-neutral context budgeting and compaction primitives."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from .contracts import ModelMessage


class ContextCompressionMode(StrEnum):
    """The host's preferred trade-off when compacting older context."""

    BALANCED = "balanced"
    PRESERVE_DETAILS = "preserve_details"
    HANDOFF = "handoff"


class ContextBudget(BaseModel):
    """Token thresholds used by :class:`ContextEngine`."""

    max_tokens: int = Field(default=12_000, ge=256)
    soft_limit_tokens: int = Field(default=8_400, ge=128)
    hard_limit_tokens: int = Field(default=10_200, ge=256)
    keep_recent_messages: int = Field(default=8, ge=1, le=100)

    @model_validator(mode="after")
    def validate_thresholds(self) -> ContextBudget:
        if not self.soft_limit_tokens < self.hard_limit_tokens <= self.max_tokens:
            raise ValueError(
                "context thresholds must satisfy soft_limit < hard_limit <= max_tokens"
            )
        return self


class ContextSectionUsage(BaseModel):
    """One named section in a context usage breakdown."""

    name: str
    tokens: int = Field(ge=0)
    estimated: bool = True


class ContextUsage(BaseModel):
    """A portable context size report suitable for APIs and UI."""

    total_tokens: int = Field(ge=0)
    budget_tokens: int = Field(ge=1)
    soft_limit_tokens: int = Field(ge=1)
    hard_limit_tokens: int = Field(ge=1)
    sections: list[ContextSectionUsage] = Field(default_factory=list)
    estimated: bool = True
    state: Literal["normal", "warning", "needs_compression"] = "normal"

    @model_validator(mode="after")
    def derive_state(self) -> ContextUsage:
        if self.total_tokens >= self.hard_limit_tokens:
            self.state = "needs_compression"
        elif self.total_tokens >= self.soft_limit_tokens:
            self.state = "warning"
        else:
            self.state = "normal"
        return self


class ContextSummary(BaseModel):
    """Stable, inspectable fields retained after older messages are compacted."""

    goal: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    current_state: str = ""
    open_items: list[str] = Field(default_factory=list)
    tool_findings: list[str] = Field(default_factory=list)


class ContextBuildResult(BaseModel):
    """The next model input plus before/after measurements."""

    messages: list[ModelMessage]
    summary: ContextSummary | None = None
    usage_before: ContextUsage
    usage_after: ContextUsage
    compressed: bool = False
    mode: ContextCompressionMode = ContextCompressionMode.BALANCED
    compressed_message_count: int = Field(default=0, ge=0)


class ContextSummarizer(Protocol):
    async def summarize(
        self,
        messages: Sequence[ModelMessage],
        *,
        mode: ContextCompressionMode,
    ) -> ContextSummary: ...


def estimate_tokens(text: str) -> int:
    """Return a conservative, dependency-free token estimate."""

    return max(1, math.ceil(len(text or "") / 4))


def _message_tokens(message: ModelMessage) -> int:
    payload = message.content or ""
    if message.tool_calls:
        payload += json.dumps(
            [call.model_dump(mode="json") for call in message.tool_calls],
            ensure_ascii=False,
            sort_keys=True,
        )
    return estimate_tokens(payload)


def _normalize_summary(summary: ContextSummary) -> ContextSummary:
    """Keep a provider response from replacing history with another giant prompt."""

    values = summary.model_dump(mode="python")
    for field in ("goal", "constraints", "decisions", "facts", "open_items", "tool_findings"):
        values[field] = [str(item)[:240] for item in values[field][:4]]
    values["current_state"] = str(values["current_state"])[:400]
    return ContextSummary.model_validate(values)


class ExtractiveContextSummarizer:
    """Deterministic fallback that never needs a model or network call."""

    async def summarize(
        self,
        messages: Sequence[ModelMessage],
        *,
        mode: ContextCompressionMode,
    ) -> ContextSummary:
        user_messages = [m.content.strip() for m in messages if m.role == "user" and m.content.strip()]
        assistant_messages = [
            m.content.strip() for m in messages if m.role == "assistant" and m.content.strip()
        ]
        tool_messages = [m.content.strip() for m in messages if m.role == "tool" and m.content.strip()]

        def matching(words: tuple[str, ...], values: list[str]) -> list[str]:
            return [value[:240] for value in values if any(word in value for word in words)][-4:]

        current = (assistant_messages or user_messages or [""])[-1][:400]
        return ContextSummary(
            goal=user_messages[:2],
            constraints=matching(("必须", "不要", "限制", "只能"), user_messages + assistant_messages),
            decisions=matching(("决定", "选择", "采用", "改为"), assistant_messages + user_messages),
            facts=[value[:240] for value in (user_messages + assistant_messages)[:4]],
            current_state=current,
            open_items=matching(("待", "还需", "需要", "未完成", "下一步"), assistant_messages + user_messages),
            tool_findings=[value[:240] for value in tool_messages[-4:]],
        )


class ContextEngine:
    """Measure and compact a message list without owning persistence."""

    def __init__(self, summarizer: ContextSummarizer | None = None) -> None:
        self._summarizer = summarizer or ExtractiveContextSummarizer()

    def measure(
        self,
        messages: Sequence[ModelMessage],
        *,
        summary: ContextSummary | None = None,
        page_context: str | None = None,
        budget: ContextBudget | None = None,
    ) -> ContextUsage:
        active_budget = budget or ContextBudget()
        system_tokens = 0
        embedded_summary_tokens = 0
        embedded_page_tokens = 0
        for message in messages:
            if message.role != "system":
                continue
            if message.content.startswith("以下是较早对话的结构化摘要"):
                embedded_summary_tokens += _message_tokens(message)
            elif message.content.startswith("页面上下文:"):
                embedded_page_tokens += _message_tokens(message)
            else:
                system_tokens += _message_tokens(message)
        non_system = [message for message in messages if message.role != "system"]
        recent_start = max(0, len(non_system) - active_budget.keep_recent_messages)
        older_tokens = sum(_message_tokens(message) for message in non_system[:recent_start])
        recent_tokens = sum(_message_tokens(message) for message in non_system[recent_start:])
        summary_tokens = embedded_summary_tokens or (estimate_tokens(summary.model_dump_json()) if summary else 0)
        page_tokens = embedded_page_tokens or (estimate_tokens(page_context) if page_context else 0)
        sections = [
            ContextSectionUsage(name="system", tokens=system_tokens),
            ContextSectionUsage(name="summary", tokens=summary_tokens),
            ContextSectionUsage(name="page_context", tokens=page_tokens),
            ContextSectionUsage(name="history", tokens=older_tokens),
            ContextSectionUsage(name="recent_messages", tokens=recent_tokens),
        ]
        return ContextUsage(
            total_tokens=sum(section.tokens for section in sections),
            budget_tokens=active_budget.max_tokens,
            soft_limit_tokens=active_budget.soft_limit_tokens,
            hard_limit_tokens=active_budget.hard_limit_tokens,
            sections=sections,
        )

    async def prepare(
        self,
        messages: Sequence[ModelMessage],
        *,
        existing_summary: ContextSummary | None = None,
        mode: ContextCompressionMode = ContextCompressionMode.BALANCED,
        force_compress: bool = False,
        page_context: str | None = None,
        budget: ContextBudget | None = None,
    ) -> ContextBuildResult:
        active_budget = budget or ContextBudget()
        original = [message.model_copy(deep=True) for message in messages]
        usage_before = self.measure(
            original,
            summary=existing_summary,
            page_context=page_context,
            budget=active_budget,
        )
        should_compress = force_compress or usage_before.total_tokens >= active_budget.soft_limit_tokens
        system_messages = [message for message in original if message.role == "system"]
        history = [message for message in original if message.role != "system"]
        older = history[:-active_budget.keep_recent_messages]
        recent = history[-active_budget.keep_recent_messages:]

        def build_messages(summary: ContextSummary | None) -> list[ModelMessage]:
            result = [message.model_copy(deep=True) for message in system_messages]
            if page_context:
                result.append(ModelMessage(role="system", content=f"页面上下文:\n{page_context}"))
            if summary:
                result.append(
                    ModelMessage(
                        role="system",
                        content="以下是较早对话的结构化摘要，仅在与当前问题相关时使用:\n"
                        + summary.model_dump_json(ensure_ascii=False),
                    )
                )
            result.extend(message.model_copy(deep=True) for message in (recent if should_compress else history))
            return result

        if not should_compress or not older:
            result_messages = build_messages(existing_summary)
            return ContextBuildResult(
                messages=result_messages,
                summary=existing_summary,
                usage_before=usage_before,
                usage_after=self.measure(result_messages, budget=active_budget),
                mode=mode,
            )

        summary_input = list(older)
        if existing_summary:
            summary_input.insert(
                0,
                ModelMessage(
                    role="system",
                    content="已有摘要:\n" + existing_summary.model_dump_json(ensure_ascii=False),
                ),
            )
        try:
            summary = await self._summarizer.summarize(summary_input, mode=mode)
        except Exception:
            summary = await ExtractiveContextSummarizer().summarize(summary_input, mode=mode)
        summary = _normalize_summary(summary)
        result_messages = build_messages(summary)
        return ContextBuildResult(
            messages=result_messages,
            summary=summary,
            usage_before=usage_before,
            usage_after=self.measure(result_messages, budget=active_budget),
            compressed=True,
            mode=mode,
            compressed_message_count=len(older),
        )
