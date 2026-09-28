"""Default policies that keep generic runtime hosts safe by default."""

from __future__ import annotations

from .contracts import RunRequest, ToolCall, ToolPermissionDecision, ToolRisk, ToolSpec


class ReadOnlyToolPolicy:
    """Expose and execute only non-confirmation read tools.

    Hosts that need writes or external effects must explicitly inject their
    own policy.  This makes adding a risky tool to a registry safe until the
    host has also designed its approval flow.
    """

    def is_tool_visible(self, _request: RunRequest, tool: ToolSpec) -> bool:
        return tool.risk is ToolRisk.READ and not tool.confirmation_required

    async def decide(
        self, request: RunRequest, tool: ToolSpec, _call: ToolCall
    ) -> ToolPermissionDecision:
        if self.is_tool_visible(request, tool):
            return ToolPermissionDecision.allow()
        return ToolPermissionDecision.deny("只读运行时不允许执行此工具")
