"""Model-backed and deterministic context summary adapters for the assistant host."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pan_agent import ContextCompressionMode, ContextSummary, ModelMessage


_SUMMARY_SYSTEM_PROMPT = """你是对话上下文压缩器。
只输出一个合法 JSON 对象，不要输出 Markdown、解释或额外文本。
JSON 必须包含字段：goal、constraints、decisions、facts、current_state、open_items、tool_findings。
其中前六个列表字段使用字符串数组，current_state 使用字符串；只保留对后续回答有帮助的事实。
不要补造没有出现在对话中的价格、日期、人物或决定。
"""


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return text


class FailoverContextSummarizer:
    """Use the host's configured failover client for structured summaries."""

    def __init__(self, client: Any, *, temperature: float = 0.1) -> None:
        self._client = client
        self._temperature = temperature

    async def summarize(
        self,
        messages: Sequence[ModelMessage],
        *,
        mode: ContextCompressionMode,
    ) -> ContextSummary:
        transcript = "\n\n".join(
            f"[{message.role}] {message.content}" for message in messages if message.content
        )
        raw = await self._client.chat_multi(
            [
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"压缩模式：{mode.value}\n"
                        "请把下面的较早对话整理成可继续使用的结构化摘要。\n\n"
                        + transcript
                    ),
                },
            ],
            temperature=self._temperature,
        )
        payload = raw if isinstance(raw, dict) else json.loads(_strip_json_fence(str(raw)))
        return ContextSummary.model_validate(payload)
