"""Guard assistant answers against unexecuted mutation claims.

Models can produce a confident natural-language completion even when they did
not emit a tool call.  This module keeps that semantic check at the PanWatch
host boundary: the generic PanAgent runtime remains responsible for execution,
while the host decides which tools have business-side write effects.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


UNVERIFIED_MUTATION_MESSAGE = (
    "我没有执行写入操作，因为本轮没有收到对应工具的成功结果。"
)

# Only match completion language, not a discussion of how an operation could
# be performed or an explicit failure such as ``更新没有成功``.  The check is
# intentionally conservative: a false positive yields a retryable error,
# while a false negative would present a fake write.
_MUTATION_VERBS = r"(?:创建|新增|修改|更新|删除|重命名|设置|启用|停用|改成)"
_COMPLETION_PREFIXES = (
    r"(?:已(?:经)?|成功(?:地)?|顺利|完成(?:了)?|搞定(?:了)?|"
    r"执行(?:完毕|完成)?)"
)
_COMPLETION_SUFFIXES = r"(?:成功|完成(?:了)?|好了|已生效|已完成)"
_NON_COMPLETION = (
    r"(?:没有|未|不|失败|无法|尚未|未能|建议|可以|需要|应该|将要?|"
    r"准备|开始|正在|尝试|计划|可能|是否|希望|想要?)"
)
_MUTATION_CLAIM_PREFIX_PATTERN = re.compile(
    rf"{_COMPLETION_PREFIXES}(?:(?!{_NON_COMPLETION}).){{0,24}}{_MUTATION_VERBS}",
    re.IGNORECASE,
)
_MUTATION_CLAIM_SUFFIX_PATTERN = re.compile(
    rf"{_MUTATION_VERBS}(?:(?!{_NON_COMPLETION}).){{0,12}}{_COMPLETION_SUFFIXES}",
    re.IGNORECASE,
)


def contains_mutation_claim(answer: str) -> bool:
    """Return whether an answer states that a write-like action completed."""
    text = answer or ""
    return bool(
        _MUTATION_CLAIM_PREFIX_PATTERN.search(text)
        or _MUTATION_CLAIM_SUFFIX_PATTERN.search(text)
    )


def has_successful_mutation_tool(
    completed_tools: Iterable[dict[str, Any]],
    mutation_tool_names: set[str],
) -> bool:
    """Check actual successful tool events against host-owned write tools."""
    return any(
        bool(event.get("ok")) and str(event.get("name") or "") in mutation_tool_names
        for event in completed_tools
    )


def unverified_mutation_claim(
    answer: str,
    completed_tools: Iterable[dict[str, Any]],
    mutation_tool_names: set[str],
) -> str | None:
    """Return a stable error message when a write claim lacks tool evidence."""
    if not contains_mutation_claim(answer):
        return None
    if has_successful_mutation_tool(completed_tools, mutation_tool_names):
        return None
    return UNVERIFIED_MUTATION_MESSAGE
