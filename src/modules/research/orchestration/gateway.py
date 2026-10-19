"""Request-scoped model metadata and attempt accounting over the existing candidate route."""
import asyncio

from pan_investment_research import ModelReply

from src.platform.ai.ai_failover import _mark_fail, _mark_ok, classify_ai_error, ERR_FATAL


class ResearchModelGateway:
    def __init__(self, client):
        self.client = client

    async def complete(self, *, role, messages, tools, schema, max_output_tokens, timeout, before_attempt):
        candidates = self.client._iter_candidates() if hasattr(self.client, "_iter_candidates") else [(self.client, self.client.model)]
        last_error = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        for client, label in candidates:
            await before_attempt()
            try:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError("research model deadline")
                kwargs = {"model": client.model, "messages": messages, "max_tokens": max_output_tokens}
                if client.model.startswith("glm-5.3"):
                    kwargs["reasoning_effort"] = "low"
                    if not tools:
                        kwargs["response_format"] = {"type": "json_object"}
                if tools:
                    kwargs["tools"] = tools
                reply = await client.client.with_options(max_retries=0, timeout=remaining).chat.completions.create(**kwargs)
                _mark_ok(label)
                message = reply.choices[0].message
                usage = {"input_tokens": reply.usage.prompt_tokens, "output_tokens": reply.usage.completion_tokens} if reply.usage else {}
                calls = [c.model_dump() for c in (message.tool_calls or [])]
                return ModelReply(message.content or "", str(getattr(reply, "model", None) or label), calls, usage, reply.choices[0].finish_reason or "")
            except Exception as exc:
                if classify_ai_error(exc) == ERR_FATAL:
                    raise
                _mark_fail(label)
                last_error = exc
        raise RuntimeError("All configured research model candidates failed") from last_error


class EvidenceTools:
    def __init__(self, details):
        self.details = details

    def schemas(self, role):
        if role not in {"technical", "flow", "events"}:
            return []
        keys = sorted({e.stock_key for e in self.details if e.domain == role})
        if not keys:
            return []
        return [{"type": "function", "function": {
            "name": f"read_{role}_details", "description": "Read complete captured source details in ONE batch. Include all required target securities in stock_keys instead of separate calls. Read-only, no new market time.",
            "parameters": {"type": "object", "properties": {"stock_keys": {
                "type": "array", "items": {"type": "string", "enum": keys}, "minItems": 1, "uniqueItems": True}},
                           "required": ["stock_keys"], "additionalProperties": False}}}]

    async def execute(self, name, arguments, *, stock_keys):
        if not isinstance(arguments, dict):
            raise ValueError("detail query arguments must be an object")
        # Accept the previous single-target shape for in-flight model responses.
        requested = arguments.get("stock_keys") if set(arguments) == {"stock_keys"} else (
            [arguments["stock_key"]] if set(arguments) == {"stock_key"} else None)
        if not isinstance(requested, list) or not requested or any(
            not isinstance(key, str) or key not in stock_keys for key in requested
        ) or len(requested) != len(set(requested)):
            raise ValueError("detail query outside research scope")
        domain = name.removeprefix("read_").removesuffix("_details")
        if name != f"read_{domain}_details" or domain not in {"technical", "flow", "events"}:
            raise ValueError("unsupported research tool")
        return [e for e in self.details if e.stock_key in requested and e.domain == domain]
