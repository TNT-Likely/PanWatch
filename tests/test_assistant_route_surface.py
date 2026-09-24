from pathlib import Path


def test_application_exposes_only_the_canonical_assistant_surface():
    from src.bootstrap.application import app

    paths = set(app.openapi().get("paths", {}))

    assert "/api/assistant/conversations" in paths
    assert "/api/assistant/suggested-questions" in paths
    assert not any(path == "/api/chat" or path.startswith("/api/chat/") for path in paths)


def test_legacy_chat_execution_module_is_removed():
    assert not Path("src/modules/assistant/chat_api.py").exists()
