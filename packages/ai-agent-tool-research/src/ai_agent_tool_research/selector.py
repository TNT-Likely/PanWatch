"""Deterministic selection and quota enforcement for retrieved candidates."""

from __future__ import annotations

from collections import Counter

from .contracts import ToolCandidate, ToolDescriptor, ToolSelectionPolicy


def select_tools(
    candidates: list[ToolCandidate],
    descriptors: dict[str, ToolDescriptor],
    policy: ToolSelectionPolicy,
) -> list[str]:
    selected: list[str] = []
    domains: Counter[str] = Counter()

    def add(name: str) -> None:
        descriptor = descriptors.get(name)
        if descriptor is None or name in selected:
            return
        if not descriptor.enabled or descriptor.risk.value != "read":
            return
        if domains[descriptor.domain] >= policy.max_per_domain:
            return
        if len(selected) >= policy.max_selected:
            return
        selected.append(name)
        domains[descriptor.domain] += 1

    by_name = {candidate.tool_name: candidate for candidate in candidates}
    for name in policy.required_tools + policy.always_include:
        candidate = by_name.get(name)
        if candidate is not None and candidate.score >= policy.min_score:
            add(name)
    for candidate in candidates:
        if candidate.score < policy.min_score:
            continue
        add(candidate.tool_name)
    return selected
