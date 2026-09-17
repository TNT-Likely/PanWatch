from __future__ import annotations

from typing import Any

from pan_agent import ModelUsage


def _value(value: Any, name: str, default: int = 0) -> int:
    if isinstance(value, dict):
        value = value.get(name, default)
    else:
        value = getattr(value, name, default)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return default


def normalize_provider_usage(usage: Any, *, model: str | None = None) -> ModelUsage | None:
    """Normalize OpenAI-compatible usage objects without importing an SDK."""

    if usage is None:
        return None
    input_tokens = _value(usage, "prompt_tokens", _value(usage, "input_tokens"))
    output_tokens = _value(usage, "completion_tokens", _value(usage, "output_tokens"))
    total_tokens = _value(usage, "total_tokens", input_tokens + output_tokens)
    prompt_details = usage.get("prompt_tokens_details", {}) if isinstance(usage, dict) else getattr(usage, "prompt_tokens_details", None)
    completion_details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else getattr(usage, "completion_tokens_details", None)
    cached_tokens = _value(
        usage,
        "cached_input_tokens",
        _value(prompt_details, "cached_tokens"),
    )
    reasoning_tokens = _value(
        usage,
        "reasoning_output_tokens",
        _value(completion_details, "reasoning_tokens"),
    )
    usage_model = (
        usage.get("model") if isinstance(usage, dict) else getattr(usage, "model", None)
    )
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=cached_tokens,
        reasoning_output_tokens=reasoning_tokens,
        model=model or usage_model,
        source="provider",
    )
