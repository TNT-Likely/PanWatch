"""Provider-reported physical context limits, independent of optional research budgets."""
import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ContextLimit:
    window: int
    input_tokens: int | None

    def available_output(self, input_tokens):
        return self.window - input_tokens - max(128, self.window // 100)


def parse_context_limit(error):
    message = str(error).lower().replace("`", "")
    combined = re.search(
        r"(?:inputs?|prompt)\s+tokens?\s*\+\s*(?:max_new_tokens|max_tokens|max_completion_tokens)"
        r"[^\n]*?(?:<=|less than or equal to)\s*(\d+)", message)
    standard = re.search(r"maximum context (?:length|window)(?:\s+is|\s+of|:)?\s*(\d+)", message)
    cap = combined or standard
    if not cap or int(cap.group(1)) <= 0:
        return None
    count = re.search(r"(\d+)\s+(?:inputs?|prompt)\s+tokens?", message) or re.search(
        r"(\d+)\s+(?:tokens?\s+)?in (?:the )?(?:messages|prompt|input)", message)
    return ContextLimit(int(cap.group(1)), int(count.group(1)) if count else None)


def minimum_output_tokens(schema):
    properties = schema.get("properties", {})
    recommendations = properties.get("recommendations", {})
    notes = properties.get("session_notes", {})
    targets = max(recommendations.get("minItems", 0), notes.get("minItems", 0))
    if not targets:
        targets = max((len(definition.get("properties", {}).get("stock_key", {}).get("enum", []))
                       for definition in schema.get("$defs", {}).values()), default=1)
    # A complete per-target contract needs headroom; fitting a tiny JSON prefix is not recovery.
    per_target = 1024 if recommendations else 512
    return max(1024, 512 + per_target * max(1, targets))


def estimate_input_tokens(messages, tools):
    # No shared tokenizer exists across compatible providers. UTF-8 bytes deliberately
    # overestimate ordinary byte-tokenized input; provider errors give the exact retry count.
    return len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False).encode()) + 64
