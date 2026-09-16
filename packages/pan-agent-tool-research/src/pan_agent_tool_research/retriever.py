"""Explainable keyword and alias retrieval without network dependencies."""

from __future__ import annotations

import re

from .contracts import ToolCandidate, ToolDescriptor

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_FIELD_WEIGHTS = {
    "name/title": 0.35,
    "summary": 0.25,
    "keywords/aliases": 0.20,
    "examples": 0.10,
    "entities/domain": 0.10,
}


def _terms(text: str) -> set[str]:
    result: set[str] = set()
    for segment in _TOKEN_RE.findall(text.casefold()):
        if any("\u4e00" <= char <= "\u9fff" for char in segment):
            if len(segment) <= 4:
                result.add(segment)
            result.update(segment[index : index + 2] for index in range(len(segment) - 1))
            result.update(segment[index : index + 3] for index in range(len(segment) - 2))
        else:
            result.add(segment)
    return {item for item in result if item}


def _coverage(query_terms: set[str], text: str, query: str) -> float:
    if not query_terms:
        return 0.0
    normalized = text.casefold()
    if query.casefold().strip() and query.casefold().strip() in normalized:
        return 1.0
    overlap = query_terms & _terms(text)
    if not overlap:
        return 0.0
    # Two meaningful query terms are enough to make a field strongly
    # relevant. This keeps Chinese bigram matching useful without allowing a
    # long sentence to dilute every individual match into near-zero scores.
    return min(1.0, len(overlap) / 2)


class KeywordToolRetriever:
    """Rank descriptors using stable field weights and no model calls."""

    def retrieve(
        self,
        query: str,
        descriptors: list[ToolDescriptor],
        *,
        limit: int = 32,
    ) -> list[ToolCandidate]:
        query = query.strip()
        query_terms = _terms(query)
        if not query_terms:
            return []

        ranked: list[ToolCandidate] = []
        for descriptor in descriptors:
            fields = {
                "name/title": f"{descriptor.tool_name} {descriptor.title}",
                "summary": " ".join(
                    [descriptor.summary, *descriptor.use_cases, descriptor.input_summary, descriptor.output_summary]
                ),
                "keywords/aliases": " ".join(
                    [*descriptor.keywords, *descriptor.aliases, *descriptor.capabilities]
                ),
                "examples": " ".join(descriptor.examples),
                "entities/domain": " ".join(
                    [*descriptor.entities, descriptor.domain, *descriptor.requires]
                ),
            }
            matched_fields = [
                field
                for field, text in fields.items()
                if _coverage(query_terms, text, query) > 0
            ]
            if not matched_fields:
                continue
            score = sum(
                _FIELD_WEIGHTS[field] * _coverage(query_terms, fields[field], query)
                for field in fields
            )
            reasons = [
                f"命中{field}"
                for field in matched_fields
            ]
            ranked.append(
                ToolCandidate(
                    tool_name=descriptor.tool_name,
                    score=min(1.0, round(score, 6)),
                    match_reasons=reasons,
                    matched_fields=matched_fields,
                    domain=descriptor.domain,
                )
            )

        ranked.sort(key=lambda item: (-item.score, item.tool_name))
        return ranked[: max(1, min(limit, 128))]
