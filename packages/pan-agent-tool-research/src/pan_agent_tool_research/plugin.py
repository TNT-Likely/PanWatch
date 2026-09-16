"""Runtime extension that adds optional Tool Research to a model turn."""

from __future__ import annotations

import hashlib
from typing import Literal

from pan_agent import (
    BeforeModelTurnContext,
    ToolExposureDecision,
)

from .contracts import ToolResearchRequest
from .service import ToolResearchService

ToolResearchMode = Literal["shadow", "active"]


class ToolResearchPlugin:
    """Run Tool Research as a safe shadow or active tool-exposure extension."""

    name = "tool_research"

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
