"""Policy-aware tool research service used by the runtime and hosts."""

from __future__ import annotations

import time

from ..contracts import ModelMessage, RunRequest, ToolRisk
from ..ports import ToolPolicy
from ..registry import ToolRegistry
from .catalog import ToolCatalog
from .contracts import (
    ToolResearchRequest,
    ToolResearchResult,
    ToolSelectionPolicy,
)
from .retriever import KeywordToolRetriever
from .selector import select_tools


class ToolResearchService:
    """Find tools without executing them or weakening host authorization."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        catalog: ToolCatalog | None = None,
        retriever: KeywordToolRetriever | None = None,
        selection_policy: ToolSelectionPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._catalog = catalog or ToolCatalog(registry.registered_descriptors())
        self._retriever = retriever or KeywordToolRetriever()
        self._selection_policy = selection_policy or ToolSelectionPolicy()

    async def research(
        self,
        request: ToolResearchRequest,
        *,
        policy: ToolPolicy,
        runtime_request: RunRequest | None = None,
    ) -> ToolResearchResult:
        started_at = time.perf_counter()
        run_request = runtime_request or RunRequest(
            run_id="tool-research",
            messages=[ModelMessage(role="user", content=request.query or "tool research")],
            context=request.context,
        )
        descriptors = {
            descriptor.tool_name: descriptor
            for descriptor in self._catalog.descriptors()
        }
        registered = {
            entry.spec.name: entry
            for entry in self._registry.registered_entries()
        }
        eligible = []
        filters: list[str] = []
        for name, descriptor in descriptors.items():
            entry = registered.get(name)
            if entry is None:
                filters.append(f"missing_registry:{name}")
                continue
            if not descriptor.enabled:
                filters.append(f"disabled:{name}")
                continue
            if name in request.excluded_tools:
                filters.append(f"excluded:{name}")
                continue
            if request.allowed_domains and descriptor.domain not in request.allowed_domains:
                filters.append(f"domain:{name}")
                continue
            if request.required_capabilities and not set(request.required_capabilities).issubset(
                descriptor.capabilities
            ):
                filters.append(f"capability:{name}")
                continue
            if not request.include_write_tools and descriptor.risk is not ToolRisk.READ:
                filters.append(f"write:{name}")
                continue
            if not policy.is_tool_visible(run_request, entry.spec):
                filters.append(f"policy:{name}")
                continue
            eligible.append(descriptor)

        candidates = self._retriever.retrieve(
            request.query,
            eligible,
            limit=min(request.max_candidates, self._selection_policy.max_candidates),
        )
        policy = self._selection_policy.model_copy(
            update={
                "max_selected": min(request.max_selected, self._selection_policy.max_selected),
            }
        )
        selected = select_tools(candidates, descriptors, policy)
        elapsed_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
        return ToolResearchResult(
            candidates=candidates,
            selected_tools=selected,
            filters_applied=filters,
            registry_version=self._registry.version,
            catalog_version=self._catalog.version,
            latency_ms=elapsed_ms,
        )
