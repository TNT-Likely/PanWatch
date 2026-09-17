from types import SimpleNamespace

from pan_agent import ModelUsage
from pan_agent_token_meter import HeuristicTokenMeter, TiktokenTokenMeter, normalize_provider_usage


def test_heuristic_meter_is_explicitly_estimated():
    result = HeuristicTokenMeter().measure_text("abcdefghij", model="test-model")

    assert result.tokens == 3
    assert result.source == "estimated"
    assert result.estimated is True
    assert result.model == "test-model"


def test_provider_usage_normalization_preserves_cached_and_reasoning_tokens():
    usage = normalize_provider_usage(
        SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
        ),
        model="test-model",
    )

    assert usage == ModelUsage(
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        cached_input_tokens=80,
        reasoning_output_tokens=12,
        model="test-model",
        source="provider",
    )


def test_tiktoken_meter_can_be_injected_without_making_tiktoken_a_runtime_dependency():
    class FakeEncoding:
        def encode(self, text, disallowed_special=()):
            return list(text)

    result = TiktokenTokenMeter(encoding_name="fake", encoding=FakeEncoding()).measure_text(
        "你好",
        model="test-model",
    )

    assert result.tokens == 2
    assert result.source == "tokenizer"
    assert result.estimated is False
    assert result.tokenizer == "tiktoken:fake"
