"""Registry for host-provided tools, filtered by a trusted policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .contracts import RunRequest, ToolExposure, ToolResult, ToolSpec
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

    def register(
        self,
        spec: ToolSpec,
        executor: ToolExecutor,
    ) -> None:
        if spec.name in self._tools:
            raise DuplicateToolName(f"tool {spec.name!r} is already registered")
        self._tools[spec.name] = RegisteredTool(
            spec=spec,
            executor=executor,
        )

    def model_tools(
        self,
        request: RunRequest,
        policy: ToolPolicy,
        *,
        names: list[str] | None = None,
        include_deferred: bool = False,
    ) -> list[ToolSpec]:
        """Return only tools that the trusted policy lets the model discover."""
        allowed_names = set(names) if names is not None else None
        explicitly_allowed = set(request.context.get("allowed_tool_names") or ())
        return [
            tool.spec
            for tool in self._tools.values()
            if (allowed_names is None or tool.spec.name in allowed_names)
            and tool.spec.exposure is not ToolExposure.HIDDEN
            and (
                tool.spec.exposure is not ToolExposure.DEFERRED
                or include_deferred
                or tool.spec.name in explicitly_allowed
            )
            and policy.is_tool_visible(request, tool.spec)
        ]

    def registered_tools(self) -> list[ToolSpec]:
        """Expose host metadata for settings UIs without exposing executors."""
        return [tool.spec for tool in self._tools.values()]

    def set_exposure(self, name: str, exposure: ToolExposure) -> None:
        """Change model exposure without replacing the registered executor."""
        entry = self.get(name)
        self._tools[name] = RegisteredTool(
            spec=entry.spec.model_copy(update={"exposure": exposure}),
            executor=entry.executor,
        )

    @property
    def version(self) -> str:
        payload = [
            {
                "name": entry.spec.name,
                "spec": entry.spec.model_dump(mode="json"),
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
