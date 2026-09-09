"""Host-supplied ports used by the framework-free runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from .contracts import ModelMessage, ModelTurn, RunRequest, RuntimeEvent, ToolResult, ToolSpec


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


class EventSink(Protocol):
    async def publish(self, event: RuntimeEvent) -> None: ...

