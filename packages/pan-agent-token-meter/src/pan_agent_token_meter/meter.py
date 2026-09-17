from __future__ import annotations

import math
from typing import Any

from pan_agent import TokenMeasurement


class HeuristicTokenMeter:
    """Dependency-free fallback used when no model tokenizer is available."""

    def measure_text(self, text: str, *, model: str | None = None) -> TokenMeasurement:
        return TokenMeasurement(
            tokens=max(1, math.ceil(len(text or "") / 4)),
            source="estimated",
            estimated=True,
            model=model,
        )


class TiktokenTokenMeter:
    """Optional local tokenizer implementation for OpenAI-compatible models."""

    def __init__(self, encoding_name: str = "cl100k_base", *, encoding: Any = None) -> None:
        if encoding is None:
            try:
                import tiktoken
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise RuntimeError(
                    "TiktokenTokenMeter requires the pan-agent-token-meter[tiktoken] extra"
                ) from exc
            encoding = tiktoken.get_encoding(encoding_name)
        self._encoding = encoding
        self._encoding_name = encoding_name

    def measure_text(self, text: str, *, model: str | None = None) -> TokenMeasurement:
        tokens = len(self._encoding.encode(text or "", disallowed_special=()))
        return TokenMeasurement(
            tokens=max(1, tokens),
            source="tokenizer",
            estimated=False,
            model=model,
            tokenizer=f"tiktoken:{self._encoding_name}",
        )
