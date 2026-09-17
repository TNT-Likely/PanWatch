"""Runtime extension that adds optional Tool Research to a model turn."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pan_agent import (
    BeforeModelTurnContext,
    ExtensionToolContext,
    ToolExposureDecision,
    ToolResult,
    ToolSpec,
)

from .contracts import ToolResearchRequest
from .service import ToolResearchService

ToolResearchMode = Literal["shadow", "active"]


class ToolResearchPlugin:
    """Run Tool Research as a safe shadow or active tool-exposure extension."""

    name = "tool_research"
    search_tool_name = "tool_search"

    def __init__(
        self,
        service: ToolResearchService,
        *,
        mode: ToolResearchMode = "shadow",
        max_trace_candidates: int = 8,
    ) -> None:
        self._service = service
        self._mode = mode
        self._max_trace_candidates = max(1, max_trace_candidates)

    async def before_model_turn(
        self, context: BeforeModelTurnContext
    ) -> ToolExposureDecision | None:
        if self._mode == "active":
            loaded_tools = self._loaded_tool_names(context.messages)
            registered_names = [
                tool.name
                for tool in context.available_tools
                if tool.name != self.search_tool_name
            ]
            visible_names = list(dict.fromkeys([*registered_names, *loaded_tools]))
            await context.emit_event(
                "exposure",
                {
                    "mode": self._mode,
                    "direct_tools": registered_names,
                    "loaded_tools": loaded_tools,
                    "search_tool": self.search_tool_name,
                },
            )
            return ToolExposureDecision(
                tool_names=tuple(visible_names),
                additional_tools=(self._search_tool_spec(),),
                metadata={"loaded_tools": loaded_tools},
            )

        query = next(
            (
                message.content.strip()
                for message in reversed(context.messages)
                if message.role == "user" and message.content.strip()
            ),
            "",
        )
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        await context.emit_event(
            "started",
            {"mode": self._mode, "query_hash": query_hash},
        )
        try:
            result = await self._service.research(
                ToolResearchRequest(
                    query=query,
                    context=dict(context.request.context),
                ),
                policy=context.policy,
                runtime_request=context.request,
            )
            await context.emit_event(
                "candidates_scored",
                {
                    "mode": self._mode,
                    "candidates": [
                        candidate.model_dump(mode="json")
                        for candidate in result.candidates[: self._max_trace_candidates]
                    ],
                },
            )
            await context.emit_event(
                "completed",
                {
                    "mode": self._mode,
                    "selected_tools": result.selected_tools,
                    "candidate_count": len(result.candidates),
                    "filters_applied": len(result.filters_applied),
                    "registry_version": result.registry_version,
                    "catalog_version": result.catalog_version,
                    "latency_ms": result.latency_ms,
                },
            )
            if self._mode == "active" and result.selected_tools:
                return ToolExposureDecision(
                    tool_names=tuple(result.selected_tools),
                    metadata={"catalog_version": result.catalog_version},
                )
        except Exception as exc:  # noqa: BLE001 - plugin failure must fail open
            await context.emit_event(
                "fallback",
                {
                    "mode": self._mode,
                    "reason": "research_failed",
                    "error_type": type(exc).__name__,
                },
            )
        return None

    async def handle_tool_call(self, context: ExtensionToolContext) -> ToolResult | None:
        if context.call.name != self.search_tool_name:
            return None
        query = str(context.call.arguments.get("query") or "").strip()
        if not query:
            return ToolResult.failure(
                summary="工具搜索需要提供 query。", error_code="invalid_tool_search_query"
            )
        try:
            result = await self._service.research(
                ToolResearchRequest(
                    query=query,
                    context=dict(context.request.context),
                    max_candidates=int(context.call.arguments.get("limit") or 8),
                ),
                policy=context.policy,
                runtime_request=context.request,
            )
        except Exception as exc:  # noqa: BLE001 - search is a fail-open boundary
            await context.emit_event(
                "fallback",
                {"reason": "search_failed", "error_type": type(exc).__name__},
            )
            return ToolResult.success(
                summary="工具搜索暂时不可用，继续使用当前已提供的工具。",
                data={"loaded_tools": [], "candidates": [], "fallback": True},
                sources=[],
                observed_at=datetime.now(UTC),
            )
        await context.emit_event(
            "searched",
            {
                "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
                "candidate_count": len(result.candidates),
                "selected_tools": result.selected_tools,
                "catalog_version": result.catalog_version,
                "latency_ms": result.latency_ms,
            },
        )
        return ToolResult.success(
            summary=(
                f"已找到并加载 {len(result.selected_tools)} 个工具。"
                if result.selected_tools
                else "没有找到匹配的可用工具。"
            ),
            data={
                "loaded_tools": result.selected_tools,
                "candidates": [
                    {
                        "tool_name": candidate.tool_name,
                        "score": candidate.score,
                        "reasons": candidate.match_reasons,
                    }
                    for candidate in result.candidates[: self._max_trace_candidates]
                ],
                "catalog_version": result.catalog_version,
            },
            sources=[],
            observed_at=datetime.now(UTC),
        )

    @classmethod
    def _search_tool_spec(cls) -> ToolSpec:
        return ToolSpec(
            name=cls.search_tool_name,
            title="搜索可用工具",
            description=(
                "当当前工具列表中没有直接匹配的能力时，搜索并加载可用工具。"
                "返回的工具会在下一轮模型调用中提供完整参数定义。"
            ),
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要完成的能力或任务，例如查询龙虎榜、分析基本面",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                        "default": 5,
                    },
                },
            },
        )

    @staticmethod
    def _loaded_tool_names(messages) -> list[str]:
        loaded: list[str] = []
        for message in messages:
            if message.role != "tool" or message.name != ToolResearchPlugin.search_tool_name:
                continue
            try:
                payload = json.loads(message.content)
                data = payload.get("data") or {}
            except (TypeError, json.JSONDecodeError):
                continue
            for name in data.get("loaded_tools") or []:
                if isinstance(name, str) and name not in loaded:
                    loaded.append(name)
        return loaded
