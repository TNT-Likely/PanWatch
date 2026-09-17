from pan_agent import ContextBudget, ContextEngine, ModelMessage, TokenMeasurement


class FixedTokenMeter:
    def measure_text(self, text: str, *, model: str | None = None) -> TokenMeasurement:
        return TokenMeasurement(
            tokens=max(1, len(text) // 10),
            source="tokenizer",
            model=model,
            tokenizer="fixed-test",
        )


def test_context_engine_accepts_an_optional_token_meter_plugin():
    usage = ContextEngine(token_meter=FixedTokenMeter(), model="test-model").measure(
        [ModelMessage(role="user", content="abcdefghij")],
        budget=ContextBudget(),
    )

    assert usage.total_tokens == 1
    assert usage.measurement == "tokenizer"
    assert usage.estimated is False
    assert usage.model == "test-model"
    assert usage.tokenizer == "fixed-test"
