"""PanWatch's AI failover adapter for the generic PanAgent model port."""

from __future__ import annotations

import json
from typing import Any

from pan_agent import ModelMessage, ModelTurn, ToolCall, ToolSpec


class FailoverModelAdapter:
    """Adapt the existing failover client without leaking it into PanAgent."""

    def __init__(self, client: Any, *, temperature: float = 0.5) -> None:
        self._client = client
        self._temperature = temperature

    async def run_turn(self, messages: list[ModelMessage], tools: list[ToolSpec], emit_token) -> ModelTurn:
        response = await self._client.chat_with_tools(
            [message.model_dump(exclude_none=True) for message in messages],
            tools=[tool.openai_schema() for tool in tools],
            temperature=self._temperature,
        )
        content = response.content or ""
        if content:
            await emit_token(content)
        tool_calls: list[ToolCall] = []
        for call in response.tool_calls or []:
            raw_arguments = call.function.arguments or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        return ModelTurn(content=content, tool_calls=tool_calls)

