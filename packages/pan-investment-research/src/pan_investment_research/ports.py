"""Host ports. The model must acquire the attempt permit before EVERY send."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .contracts import Evidence, Role

AttemptPermit = Callable[[], Awaitable[None]]


@dataclass
class ModelReply:
    content: str
    model: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""


class ResearchModel(Protocol):
    async def complete(self, *, role: Role, messages: list[dict], tools: list[dict],
                       schema: dict, max_output_tokens: int, timeout: float,
                       before_attempt: AttemptPermit) -> ModelReply: ...


class ResearchTools(Protocol):
    def schemas(self, role: Role) -> list[dict]: ...
    async def execute(self, name: str, arguments: dict, *, stock_keys: list[str]) -> list[Evidence]: ...


class ResearchEvents(Protocol):
    async def publish(self, event: dict) -> None: ...
