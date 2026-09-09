"""PanWatch business adapters for the framework-free PanAgent runtime."""

from __future__ import annotations

from datetime import UTC, datetime

from pan_agent import RunRequest, ToolRegistry, ToolResult, ToolRisk, ToolSpec
from sqlalchemy.orm import Session

from src.modules.portfolio import build_portfolio_service


def build_panwatch_tool_registry(session: Session) -> ToolRegistry:
    """Register only host-approved, read-only tools for an assistant run."""
    registry = ToolRegistry()
    portfolio_service = build_portfolio_service(session)

    async def get_portfolio(_request: RunRequest, _arguments: dict) -> ToolResult:
        summary = portfolio_service.build_assistant_summary() or "用户暂无持仓。"
        return ToolResult.success(
            summary=summary,
            data={"has_positions": summary != "用户暂无持仓。"},
            sources=[{"name": "PanWatch 持仓"}],
            observed_at=datetime.now(UTC),
        )

    registry.register(
        ToolSpec(
            name="get_portfolio",
            title="查询持仓",
            description="查询用户的实盘和模拟盘持仓摘要。",
            risk=ToolRisk.READ,
            input_schema={"type": "object", "properties": {}},
        ),
        get_portfolio,
    )
    return registry
