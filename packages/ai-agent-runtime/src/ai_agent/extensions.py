"""Optional extension hooks for the provider-neutral Agent runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .contracts import ModelMessage, RunRequest, ToolCall, ToolResult, ToolSpec
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
class ExtensionToolContext:
    """Context for a virtual tool owned by a runtime extension."""

    request: RunRequest
    messages: Sequence[ModelMessage]
    call: ToolCall
    tool: ToolSpec
    available_tools: Sequence[ToolSpec]
    policy: ToolPolicy
    emit_event: ExtensionEventEmitter


@dataclass(frozen=True)
class ToolExposureDecision:
    """Optional model-tool changes made by a runtime extension.

    ``tool_names`` selects registered tools and may include deferred tools
    explicitly loaded by the extension. ``additional_tools`` are virtual
    extension-owned tools such as ``tool_search``; they are handled through
    ``RuntimeExtension.handle_tool_call`` instead of the host registry.
    """

    tool_names: Sequence[str] | None = None
    additional_tools: Sequence[ToolSpec] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeExtension(Protocol):
    """A host- or package-provided hook around a model turn."""

    name: str

    async def before_model_turn(
        self, context: BeforeModelTurnContext
    ) -> ToolExposureDecision | None: ...

    async def handle_tool_call(
        self, context: ExtensionToolContext
    ) -> ToolResult | None: ...
