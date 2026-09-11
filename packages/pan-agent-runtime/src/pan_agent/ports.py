"""Host-supplied ports used by the framework-free runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from .contracts import (
    ModelMessage,
    ModelTurn,
    RunRequest,
    RuntimeEvent,
    ToolCall,
    ToolPermissionDecision,
    ToolResult,
    ToolSpec,
)

TokenEmitter = Callable[[str], Awaitable[None]]


class ModelPort(Protocol):
    async def run_turn(
        self,
        messages: list[ModelMessage],
        tools: list[ToolSpec],
        emit_token: TokenEmitter,
    ) -> ModelTurn: ...


class ToolExecutor(Protocol):
    async def __call__(self, request: RunRequest, arguments: dict) -> ToolResult: ...


class ToolPolicy(Protocol):
    """Trusted host policy for model exposure and each tool invocation."""

    def is_tool_visible(self, request: RunRequest, tool: ToolSpec) -> bool: ...

    async def decide(
        self, request: RunRequest, tool: ToolSpec, call: ToolCall
    ) -> ToolPermissionDecision: ...


class EventSink(Protocol):
    async def publish(self, event: RuntimeEvent) -> None: ...
