import pytest


def test_context_settings_have_safe_defaults_and_can_be_overridden(monkeypatch):
    from src.platform.runtime.config import Settings

    defaults = Settings(_env_file=None)
    assert defaults.context_compression_model_id is None
    assert defaults.context_compression_temperature == 0.1
    assert defaults.context_max_tokens == 12000
    assert defaults.context_soft_limit_tokens == 8400
    assert defaults.context_hard_limit_tokens == 10200
    assert defaults.context_keep_recent_messages == 8

    monkeypatch.setenv("CONTEXT_COMPRESSION_MODEL_ID", "7")
    monkeypatch.setenv("CONTEXT_COMPRESSION_TEMPERATURE", "0.2")
    monkeypatch.setenv("CONTEXT_MAX_TOKENS", "16000")
    monkeypatch.setenv("CONTEXT_SOFT_LIMIT_TOKENS", "10000")
    monkeypatch.setenv("CONTEXT_HARD_LIMIT_TOKENS", "14000")
    monkeypatch.setenv("CONTEXT_KEEP_RECENT_MESSAGES", "6")
    configured = Settings(_env_file=None)

    assert configured.context_compression_model_id == 7
    assert configured.context_compression_temperature == 0.2
    assert configured.context_max_tokens == 16000
    assert configured.context_soft_limit_tokens == 10000
    assert configured.context_hard_limit_tokens == 14000
    assert configured.context_keep_recent_messages == 6


def test_context_settings_reject_inconsistent_thresholds():
    from src.platform.runtime.config import Settings

    with pytest.raises(ValueError, match="soft_limit"):
        Settings(
            _env_file=None,
            context_max_tokens=1000,
            context_soft_limit_tokens=900,
            context_hard_limit_tokens=800,
        )
