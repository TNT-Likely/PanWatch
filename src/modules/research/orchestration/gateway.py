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
        return [{"type": "function", "function": {
            "name": f"read_{role}_details", "description": "Read complete captured source details for a target security; read-only, no new market time.",
            "parameters": {"type": "object", "properties": {"stock_key": {"type": "string"}},
                           "required": ["stock_key"], "additionalProperties": False}}}]

    async def execute(self, name, arguments, *, stock_keys):
        if set(arguments) != {"stock_key"} or arguments["stock_key"] not in stock_keys:
            raise ValueError("detail query outside research scope")
        domain = name.removeprefix("read_").removesuffix("_details")
        if name != f"read_{domain}_details" or domain not in {"technical", "flow", "events"}:
            raise ValueError("unsupported research tool")
        return [e for e in self.details if e.stock_key == arguments["stock_key"] and e.domain == domain]
