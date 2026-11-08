"""Request-scoped model metadata and attempt accounting over the existing candidate route."""
import asyncio
import logging
import re

from openai import BadRequestError

from pan_investment_research import ModelReply

from src.platform.ai.ai_failover import _mark_fail, _mark_ok, classify_ai_error, ERR_FATAL
from .context_limits import estimate_input_tokens, minimum_output_tokens, parse_context_limit

logger = logging.getLogger(__name__)


class ResearchModelGateway:
    def __init__(self, client):
        self.client = client
        self.context_limits = {}

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

    @staticmethod
    def _adjust_options(kwargs, error):
        """Negotiate rejected options without exceeding the requested output budget."""
        message = str(error).lower()
        if "response_format" in message and "response_format" in kwargs:
            kwargs.pop("response_format")
            return True
        token_key = next((key for key in ("max_tokens", "max_completion_tokens") if key in kwargs), None)
        if not token_key or not any(key in message for key in ("max_tokens", "max_completion_tokens")):
            return False
        cap = re.search(r"(?:at most|less than or equal to|<=)\s*(\d+)|\[\s*\d+\s*,\s*(\d+)\s*\]", message)
        if cap:
            limit = int(cap.group(1) or cap.group(2))
            if 0 < limit < kwargs[token_key]:
                kwargs[token_key] = limit
                return True
        if token_key == "max_tokens" and "max_completion_tokens" in message:
            kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
            return True
        return False

    async def complete(self, *, role, messages, tools, schema, max_output_tokens, timeout, before_attempt):
        candidates = self.client._iter_candidates() if hasattr(self.client, "_iter_candidates") else [(self.client, self.client.model)]
        last_error = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout if timeout is not None else None

        async def call(client, kwargs):
            options = {"max_retries": 0}
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError("research model deadline")
                options["timeout"] = remaining
            return await client.client.with_options(**options).chat.completions.create(**kwargs)

        required_output = minimum_output_tokens(schema)
        context_errors = []
        for client, label in candidates:
            identity = (getattr(client, "base_url", ""), client.model)
            kwargs = {"model": client.model, "messages": messages}
            if max_output_tokens is not None:
                kwargs["max_tokens"] = max_output_tokens
            if tools:
                kwargs["tools"] = tools
            kwargs.update(self._extras(client.model, tools))
            known_limit = self.context_limits.get(identity)
            if known_limit:
                estimated = estimate_input_tokens(messages, tools)
                available = known_limit.available_output(estimated)
                if available < required_output:
                    context_errors.append(f"{label}: estimated input {estimated}, window {known_limit.window}")
                    logger.warning("Research context skip: role=%s candidate=%s estimated_input=%s window=%s required_output=%s",
                                   role, label, estimated, known_limit.window, required_output)
                    continue
                kwargs["max_tokens"] = min(max_output_tokens or available, available)
            context_retried = False
            for negotiation in range(5):
                await before_attempt()
                try:
                    reply = await call(client, kwargs)
                except Exception as exc:
                    limit = parse_context_limit(exc) if isinstance(exc, BadRequestError) else None
                    if limit:
                        self.context_limits[identity] = limit
                        available = limit.available_output(limit.input_tokens) if limit.input_tokens is not None else 0
                        context_errors.append(f"{label}: input {limit.input_tokens}, window {limit.window}")
                        token_key = "max_completion_tokens" if "max_completion_tokens" in kwargs else "max_tokens"
                        feasible = min(kwargs.get(token_key, available), available)
                        retry = not context_retried and feasible >= required_output and negotiation < 4
                        logger.warning("Research context limit: role=%s candidate=%s input=%s window=%s output=%s action=%s",
                                       role, label, limit.input_tokens, limit.window, feasible, "retry" if retry else "switch")
                        last_error = exc
                        if retry:
                            kwargs[token_key] = feasible
                            context_retried = True
                            continue
                        break
                    if isinstance(exc, BadRequestError) and negotiation < 4 and self._adjust_options(kwargs, exc):
                        continue
                    if classify_ai_error(exc) == ERR_FATAL:
                        raise
                    _mark_fail(label)
                    last_error = exc
                    logger.warning("Research candidate failed: role=%s candidate=%s error=%s; trying next candidate",
                                   role, label, type(exc).__name__)
                    break
                return self._reply(reply, label)
        detail = "; context capacity: " + "; ".join(context_errors) if context_errors else ""
        raise RuntimeError("All configured research model candidates failed" + detail) from last_error


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
