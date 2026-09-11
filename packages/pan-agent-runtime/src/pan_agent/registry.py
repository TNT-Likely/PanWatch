"""Registry for host-provided tools, filtered by a trusted policy."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import RunRequest, ToolResult, ToolSpec
from .errors import DuplicateToolName, UnknownTool
from .ports import ToolExecutor, ToolPolicy


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    executor: ToolExecutor


class ToolRegistry:
    """Store tool definitions while leaving authorization to the host policy."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, executor: ToolExecutor) -> None:
        if spec.name in self._tools:
            raise DuplicateToolName(f"tool {spec.name!r} is already registered")
        self._tools[spec.name] = RegisteredTool(spec=spec, executor=executor)

    def model_tools(self, request: RunRequest, policy: ToolPolicy) -> list[ToolSpec]:
        """Return only tools that the trusted policy lets the model discover."""
        return [tool.spec for tool in self._tools.values() if policy.is_tool_visible(request, tool.spec)]

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownTool(f"tool {name!r} is not registered") from exc

    async def execute(self, name: str, request: RunRequest, arguments: dict) -> ToolResult:
        return await self.get(name).executor(request, arguments)
