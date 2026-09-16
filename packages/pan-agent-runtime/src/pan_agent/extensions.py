"""Optional extension hooks for the provider-neutral Agent runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .contracts import ModelMessage, RunRequest, ToolSpec
from .ports import ToolPolicy

ExtensionEventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class BeforeModelTurnContext:
    """Read-only context handed to extensions before one model turn."""

    request: RunRequest
    messages: Sequence[ModelMessage]
    available_tools: Sequence[ToolSpec]
    policy: ToolPolicy
    emit_event: ExtensionEventEmitter


@dataclass(frozen=True)
class ToolExposureDecision:
    """Optional reduction of the policy-approved model tool set."""

    tool_names: Sequence[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeExtension(Protocol):
    """A host- or package-provided hook around a model turn."""

    name: str

    async def before_model_turn(
        self, context: BeforeModelTurnContext
    ) -> ToolExposureDecision | None: ...
