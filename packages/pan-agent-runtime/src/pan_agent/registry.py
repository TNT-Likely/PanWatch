"""Registry for host-provided tools, filtered by a trusted policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .contracts import RunRequest, ToolResult, ToolRisk, ToolSpec
from .errors import DuplicateToolName, UnknownTool
from .ports import ToolExecutor, ToolPolicy
from .tool_research.contracts import ToolDescriptor


def _risk_level(risk: ToolRisk) -> int:
    return {
        ToolRisk.READ: 0,
        ToolRisk.WRITE: 1,
        ToolRisk.EXTERNAL: 2,
        ToolRisk.DESTRUCTIVE: 3,
    }[risk]


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    executor: ToolExecutor
    descriptor: ToolDescriptor | None = None


class ToolRegistry:
    """Store tool definitions while leaving authorization to the host policy."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        spec: ToolSpec,
        executor: ToolExecutor,
        descriptor: ToolDescriptor | None = None,
    ) -> None:
        if spec.name in self._tools:
            raise DuplicateToolName(f"tool {spec.name!r} is already registered")
        if descriptor is not None and descriptor.tool_name != spec.name:
            raise ValueError("tool descriptor must match the registered tool name")
        if descriptor is not None and _risk_level(descriptor.risk) < _risk_level(spec.risk):
            raise ValueError("tool descriptor risk cannot be weaker than ToolSpec risk")
        if descriptor is not None and spec.confirmation_required and not descriptor.confirmation_required:
            raise ValueError("tool descriptor must preserve confirmation_required")
        self._tools[spec.name] = RegisteredTool(
            spec=spec,
            executor=executor,
            descriptor=descriptor,
        )

    def register_descriptor(self, descriptor: ToolDescriptor) -> None:
        """Attach search metadata to an existing executable tool."""
        entry = self._tools.get(descriptor.tool_name)
        if entry is None:
            raise UnknownTool(f"tool {descriptor.tool_name!r} is not registered")
        if entry.descriptor is not None:
            raise DuplicateToolName(
                f"descriptor for tool {descriptor.tool_name!r} is already registered"
            )
        if _risk_level(descriptor.risk) < _risk_level(entry.spec.risk):
            raise ValueError("tool descriptor risk cannot be weaker than ToolSpec risk")
        if entry.spec.confirmation_required and not descriptor.confirmation_required:
            raise ValueError("tool descriptor must preserve confirmation_required")
        self._tools[descriptor.tool_name] = RegisteredTool(
            spec=entry.spec,
            executor=entry.executor,
            descriptor=descriptor.model_copy(deep=True),
        )

    def model_tools(
        self,
        request: RunRequest,
        policy: ToolPolicy,
        *,
        names: list[str] | None = None,
    ) -> list[ToolSpec]:
        """Return only tools that the trusted policy lets the model discover."""
        allowed_names = set(names) if names is not None else None
        return [
            tool.spec
            for tool in self._tools.values()
            if (allowed_names is None or tool.spec.name in allowed_names)
            and policy.is_tool_visible(request, tool.spec)
        ]

    def registered_tools(self) -> list[ToolSpec]:
        """Expose host metadata for settings UIs without exposing executors."""
        return [tool.spec for tool in self._tools.values()]

    def registered_entries(self) -> list[RegisteredTool]:
        """Expose immutable host metadata for research adapters."""
        return list(self._tools.values())

    def registered_descriptors(self) -> list[ToolDescriptor]:
        return [
            entry.descriptor.model_copy(deep=True)
            for entry in self._tools.values()
            if entry.descriptor is not None
        ]

    @property
    def version(self) -> str:
        payload = [
            {
                "name": entry.spec.name,
                "spec": entry.spec.model_dump(mode="json"),
                "descriptor": entry.descriptor.model_dump(mode="json")
                if entry.descriptor is not None
                else None,
            }
            for entry in sorted(self._tools.values(), key=lambda item: item.spec.name)
        ]
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:12]
        return f"registry-{digest}"

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownTool(f"tool {name!r} is not registered") from exc

    async def execute(self, name: str, request: RunRequest, arguments: dict) -> ToolResult:
        return await self.get(name).executor(request, arguments)
