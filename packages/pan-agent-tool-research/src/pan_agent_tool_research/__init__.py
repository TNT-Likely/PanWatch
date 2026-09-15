"""Optional, deterministic and policy-aware Tool Research plugin."""

from .contracts import (
    ToolCandidate,
    ToolDataFreshness,
    ToolDescriptor,
    ToolResearchRequest,
    ToolResearchResult,
    ToolSelectionPolicy,
)
from .plugin import ToolResearchMode, ToolResearchPlugin

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
    "ToolResearchMode",
    "ToolResearchPlugin",
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
