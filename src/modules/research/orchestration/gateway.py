"""Request-scoped model metadata and attempt accounting over the existing candidate route."""
import asyncio
import logging
import re
from urllib.parse import urlsplit

from openai import BadRequestError

from pan_investment_research import ModelReply

from src.platform.ai.ai_failover import _mark_fail, _mark_ok, classify_ai_error, ERR_FATAL
from .context_limits import estimate_input_tokens, minimum_output_tokens, parse_context_limit

logger = logging.getLogger(__name__)


class ResearchProviderError(RuntimeError):
    """All available candidates failed or returned an unusable protocol response."""


def _responses_input(messages):
    items = []
    for message in messages:
        if message.get("response_items"):
            items.extend(message["response_items"])
        elif message["role"] == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": message["content"]})
        else:
            if message.get("content"):
                items.append({"role": message["role"], "content": message["content"]})
            items.extend({"type": "function_call", "call_id": call["id"], **call["function"]}
                         for call in message.get("tool_calls", []))
    return items


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
        if not reply.choices:
            raise ResearchProviderError("Model returned no choices")
        message = reply.choices[0].message
        usage = {"input_tokens": reply.usage.prompt_tokens, "output_tokens": reply.usage.completion_tokens} if reply.usage else {}
        calls = [c.model_dump() for c in (message.tool_calls or [])]
        return ModelReply(message.content or "", str(getattr(reply, "model", None) or label), calls, usage, reply.choices[0].finish_reason or "")

    @staticmethod
    def _responses_reply(reply, label):
        calls = [{"type": "function", "id": item.call_id,
                  "function": {"name": item.name, "arguments": item.arguments}}
                 for item in reply.output if item.type == "function_call"]
        details = getattr(reply, "incomplete_details", None)
        truncated = getattr(details, "reason", None) == "max_output_tokens"
        if reply.status != "completed" and not truncated:
            raise ResearchProviderError(f"Responses returned status {reply.status}")
        usage = {"input_tokens": reply.usage.input_tokens, "output_tokens": reply.usage.output_tokens} if reply.usage else {}
        return ModelReply(reply.output_text, reply.model or label, calls, usage,
                          "length" if truncated else "tool_calls" if calls else "stop",
                          [item.model_dump(mode="json", exclude_none=True) for item in reply.output])

    @staticmethod
    def _adjust_options(kwargs, error):
        """Negotiate rejected options without exceeding the requested output budget."""
        message = str(error).lower()
        if "response_format" in message and "response_format" in kwargs:
            kwargs.pop("response_format")
            return True
        token_keys = ("max_tokens", "max_completion_tokens", "max_output_tokens")
        token_key = next((key for key in token_keys if key in kwargs), None)
        if not token_key or not any(key in message for key in token_keys):
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

        async def call(client, kwargs, responses):
            options = {"max_retries": 0}
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError("research model deadline")
                options["timeout"] = min(remaining, 60)
            sdk = client.client.with_options(**options)
            if responses:
                return await sdk.responses.create(**kwargs)
            return await sdk.chat.completions.create(**kwargs)

        required_output = minimum_output_tokens(schema)
        context_errors = []
        for client, label in candidates:
            identity = (getattr(client, "base_url", ""), client.model)
            use_responses = (
                urlsplit(str(identity[0])).hostname == "api.openai.com"
                and client.model.lower().startswith("gpt-6")
                and (tools or any(message.get("tool_calls") for message in messages))
            )
            token_key = "max_output_tokens" if use_responses else "max_tokens"
            if use_responses:
                kwargs = {"model": client.model, "input": _responses_input(messages), "store": False,
                          "include": ["reasoning.encrypted_content"]}
                if tools:
                    kwargs["tools"] = [{"type": "function", **tool["function"]} for tool in tools]
                else:
                    kwargs["text"] = {"format": {"type": "json_object"}}
            else:
                chat_messages = [{k: v for k, v in message.items() if k != "response_items"} for message in messages]
                kwargs = {"model": client.model, "messages": chat_messages, **self._extras(client.model, tools)}
                if tools:
                    kwargs["tools"] = tools
            if max_output_tokens is not None:
                kwargs[token_key] = max_output_tokens
            known_limit = self.context_limits.get(identity)
            if known_limit:
                estimated = estimate_input_tokens(messages, tools)
                available = known_limit.available_output(estimated)
                if available < required_output:
                    context_errors.append(f"{label}: estimated input {estimated}, window {known_limit.window}")
                    logger.warning("Research context skip: role=%s candidate=%s estimated_input=%s window=%s required_output=%s",
                                   role, label, estimated, known_limit.window, required_output)
                    continue
                kwargs[token_key] = min(max_output_tokens or available, available)
            context_retried = False
            for negotiation in range(5):
                await before_attempt()
                try:
                    raw_reply = await call(client, kwargs, use_responses)
                    reply = self._responses_reply(raw_reply, label) if use_responses else self._reply(raw_reply, label)
                except Exception as exc:
                    limit = parse_context_limit(exc) if isinstance(exc, BadRequestError) else None
                    if limit:
                        self.context_limits[identity] = limit
                        available = limit.available_output(limit.input_tokens) if limit.input_tokens is not None else 0
                        context_errors.append(f"{label}: input {limit.input_tokens}, window {limit.window}")
                        capacity_key = "max_completion_tokens" if "max_completion_tokens" in kwargs else token_key
                        feasible = min(kwargs.get(capacity_key, available), available)
                        retry = not context_retried and feasible >= required_output and negotiation < 4
                        logger.warning("Research context limit: role=%s candidate=%s input=%s window=%s output=%s action=%s",
                                       role, label, limit.input_tokens, limit.window, feasible, "retry" if retry else "switch")
                        last_error = exc
                        if retry:
                            kwargs[capacity_key] = feasible
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
                _mark_ok(label)
                return reply
        detail = "; context capacity: " + "; ".join(context_errors) if context_errors else ""
        raise ResearchProviderError("All configured research model candidates failed" + detail) from last_error


class EvidenceTools:
    def __init__(self, details):
        self.details = details

    def schemas(self, role):
        return self.scoped_schemas(role, {e.stock_key for e in self.details})

    def scoped_schemas(self, role, stock_keys):
        if role not in {"technical", "flow", "events"}:
            return []
        keys = sorted({e.stock_key for e in self.details if e.domain == role and e.stock_key in stock_keys})
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
