"""In-process descriptor catalog for the first deterministic retrieval stage."""

from __future__ import annotations

import hashlib
import json

from ..contracts import ToolRisk
from ..errors import DuplicateToolName, UnknownTool
from .contracts import ToolDescriptor


class ToolCatalog:
    """Keep search metadata separate from executable registry entries."""

    def __init__(self, descriptors: list[ToolDescriptor] | None = None) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        for descriptor in descriptors or []:
            self.register(descriptor)

    def register(self, descriptor: ToolDescriptor) -> None:
        if descriptor.tool_name in self._descriptors:
            raise DuplicateToolName(
                f"descriptor for tool {descriptor.tool_name!r} is already registered"
            )
        self._descriptors[descriptor.tool_name] = descriptor.model_copy(deep=True)

    def replace(self, descriptor: ToolDescriptor) -> None:
        self._descriptors[descriptor.tool_name] = descriptor.model_copy(deep=True)

    def remove(self, tool_name: str) -> None:
        self._descriptors.pop(tool_name, None)

    def get(self, tool_name: str) -> ToolDescriptor:
        try:
            return self._descriptors[tool_name]
        except KeyError as exc:
            raise UnknownTool(f"descriptor for tool {tool_name!r} is not registered") from exc

    def descriptors(self) -> list[ToolDescriptor]:
        return [
            item.model_copy(deep=True)
            for item in sorted(self._descriptors.values(), key=lambda value: value.tool_name)
        ]

    @property
    def version(self) -> str:
        payload = [item.model_dump(mode="json") for item in self.descriptors()]
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:12]
        return f"catalog-{digest}"


def risk_level(risk: ToolRisk) -> int:
    return {
        ToolRisk.READ: 0,
        ToolRisk.WRITE: 1,
        ToolRisk.EXTERNAL: 2,
        ToolRisk.DESTRUCTIVE: 3,
    }[risk]
