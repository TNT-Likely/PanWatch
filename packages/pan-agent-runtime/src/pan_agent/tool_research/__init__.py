"""Deterministic, policy-aware tool discovery primitives.

The heavier catalog, retriever and service imports stay lazy so the registry
can attach descriptor contracts without creating an import cycle.
"""

from .contracts import (
    ToolCandidate,
    ToolDataFreshness,
    ToolDescriptor,
    ToolResearchRequest,
    ToolResearchResult,
    ToolSelectionPolicy,
)

__all__ = [
    "KeywordToolRetriever",
    "ToolCandidate",
    "ToolCatalog",
    "ToolDataFreshness",
    "ToolDescriptor",
    "ToolResearchRequest",
    "ToolResearchResult",
    "ToolResearchService",
    "ToolSelectionPolicy",
]


def __getattr__(name: str):
    if name == "ToolCatalog":
        from .catalog import ToolCatalog

        return ToolCatalog
    if name == "KeywordToolRetriever":
        from .retriever import KeywordToolRetriever

        return KeywordToolRetriever
    if name == "ToolResearchService":
        from .service import ToolResearchService

        return ToolResearchService
    raise AttributeError(name)
