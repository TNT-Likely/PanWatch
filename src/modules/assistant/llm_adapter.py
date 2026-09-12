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

    @staticmethod
    def _to_provider_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
        """Translate PanAgent's neutral tool-call history to OpenAI format."""
        provider_messages: list[dict[str, Any]] = []
        for message in messages:
            payload = message.model_dump(exclude_none=True, exclude={"tool_calls"})
            if message.tool_calls:
                # A tool result is only meaningful to an OpenAI-compatible
                # model when the preceding assistant message declares its id.
                payload["content"] = message.content or None
                payload["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(
                                call.arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                    for call in message.tool_calls
                ]
            provider_messages.append(payload)
        return provider_messages

    async def run_turn(
        self,
        messages: list[ModelMessage],
        tools: list[ToolSpec],
        emit_token,
        tool_choice: str | None = None,
    ) -> ModelTurn:
        """Run one model turn and forward every model delta immediately.

        The HTTP layer is already an SSE endpoint.  Its perceived streaming
        speed therefore depends on this adapter consuming the model client's
        tool-compatible stream rather than waiting for ``chat_with_tools`` to
        assemble the complete answer.
        """
        content_parts: list[str] = []
        final_content = ""
        raw_tool_calls: list[dict[str, Any]] = []

        stream_kwargs = {
            "tools": [tool.openai_schema() for tool in tools],
            "temperature": self._temperature,
        }
        if tool_choice is not None:
            stream_kwargs["tool_choice"] = tool_choice

        async for event_type, payload in self._client.chat_stream(
            self._to_provider_messages(messages), **stream_kwargs
        ):
            if event_type == "token":
                token = str(payload or "")
                if token:
                    content_parts.append(token)
                    # A required-tool turn is an internal proposal.  The
                    # runtime will expose the final answer after the tool
                    # result, not the model's pre-tool narration.
                    if tool_choice != "required":
                        await emit_token(token)
            elif event_type == "message" and isinstance(payload, dict):
                final_content = str(payload.get("content") or "")
                raw_tool_calls = payload.get("tool_calls") or []

        tool_calls: list[ToolCall] = []
        for call in raw_tool_calls:
            raw_arguments = call.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=str(call.get("id") or ""),
                    name=str(call.get("name") or ""),
                    arguments=arguments,
                )
            )
        return ModelTurn(content=final_content or "".join(content_parts), tool_calls=tool_calls)
