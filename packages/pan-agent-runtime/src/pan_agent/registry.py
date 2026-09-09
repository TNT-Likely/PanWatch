"""Safe registry for host-provided read-only tools."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import RunRequest, ToolResult, ToolRisk, ToolSpec
from .errors import DisallowedTool, DuplicateToolName, UnknownTool
from .ports import ToolExecutor


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    executor: ToolExecutor


class ToolRegistry:
    """Registry that enforces the runtime's read-only safety policy."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, executor: ToolExecutor) -> None:
        if spec.risk is not ToolRisk.READ or spec.confirmation_required:
            raise DisallowedTool(f"tool {spec.name!r} is not permitted in the read-only runtime")
        if spec.name in self._tools:
            raise DuplicateToolName(f"tool {spec.name!r} is already registered")
        self._tools[spec.name] = RegisteredTool(spec=spec, executor=executor)

    def model_tools(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownTool(f"tool {name!r} is not registered") from exc

    async def execute(self, name: str, request: RunRequest, arguments: dict) -> ToolResult:
        return await self.get(name).executor(request, arguments)

