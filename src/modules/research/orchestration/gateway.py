"""Request-scoped model metadata and attempt accounting over the existing candidate route."""
import asyncio
import re

from openai import BadRequestError

from pan_investment_research import ModelReply

from src.platform.ai.ai_failover import _mark_fail, _mark_ok, classify_ai_error, ERR_FATAL


class ResearchModelGateway:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def _retry_kwargs(exc, kwargs):
        """Same-candidate parameter fixes for provider-specific 400s; None gives up on it."""
        if not isinstance(exc, BadRequestError):
            return None
        message = str(exc).lower()
        if "response_format" in message and "response_format" in kwargs:
            fixed = dict(kwargs)
            fixed.pop("response_format")
            return fixed
        if "max_tokens" in message:
            # Providers advertise the accepted range in several shapes, e.g.
            # "限制数值范围[1,1024]" — clamp to the advertised bound and retry.
            bound = None
            for pattern in (r"\[1,(\d+)\]", r"1\s*[-~到]\s*(\d+)"):
                found = re.search(pattern, str(exc))
                if found:
                    bound = int(found.group(1))
                    break
            if bound and bound < kwargs.get("max_tokens", 0):
                fixed = dict(kwargs)
                fixed["max_tokens"] = bound
                return fixed
        return None

    @staticmethod
    def _extras(model, tools):
        # Research roles always answer with one JSON object; request JSON mode from every
        # OpenAI-compatible endpoint unless tool schemas occupy the response. Endpoints that
        # reject it fall back to the bare request below.
        extras = {}
        if not tools:
            extras["response_format"] = {"type": "json_object"}
        if "glm-5.3" in model.lower():
            extras["reasoning_effort"] = "low"
        return extras

    @staticmethod
    def _reply(reply, label):
        _mark_ok(label)
        message = reply.choices[0].message
        usage = {"input_tokens": reply.usage.prompt_tokens, "output_tokens": reply.usage.completion_tokens} if reply.usage else {}
        calls = [c.model_dump() for c in (message.tool_calls or [])]
        return ModelReply(message.content or "", str(getattr(reply, "model", None) or label), calls, usage, reply.choices[0].finish_reason or "")

    async def complete(self, *, role, messages, tools, schema, max_output_tokens, timeout, before_attempt):
        candidates = self.client._iter_candidates() if hasattr(self.client, "_iter_candidates") else [(self.client, self.client.model)]
        last_error = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        async def call(client, kwargs):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("research model deadline")
            return await client.client.with_options(max_retries=0, timeout=remaining).chat.completions.create(**kwargs)

        for client, label in candidates:
            await before_attempt()
            kwargs = {"model": client.model, "messages": messages, "max_tokens": max_output_tokens}
            if tools:
                kwargs["tools"] = tools
            full = {**kwargs, **self._extras(client.model, tools)}
            try:
                reply = await call(client, full)
            except Exception as exc:
                fixed = self._retry_kwargs(exc, full)
                if fixed is None:
                    if classify_ai_error(exc) == ERR_FATAL:
                        raise
                    _mark_fail(label)
                    last_error = exc
                    continue
                # Provider-specific parameter rejection: retry this candidate once, adjusted.
                await before_attempt()
                try:
                    reply = await call(client, fixed)
                except Exception as retry_exc:
                    if classify_ai_error(retry_exc) == ERR_FATAL:
                        raise
                    _mark_fail(label)
                    last_error = retry_exc
                    continue
                return self._reply(reply, label)
            return self._reply(reply, label)
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
