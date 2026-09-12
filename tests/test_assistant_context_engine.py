import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import AIModel, AIService, ChatConversation  # noqa: F401


def _service(settings=None):
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.service import AssistantService

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return engine, session, AssistantService(AssistantRepository(session), settings=settings)


def _settings(**overrides):
    values = {
        "context_compression_model_id": None,
        "context_compression_temperature": 0.1,
        "context_max_tokens": 400,
        "context_soft_limit_tokens": 128,
        "context_hard_limit_tokens": 256,
        "context_keep_recent_messages": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_service_selects_configured_compression_model(monkeypatch):
    from src.modules.assistant.schemas import CreateConversationCommand

    engine, session, service = _service(_settings(context_compression_model_id=9))
    calls = []

    def fake_get_configured(db, model_id):
        calls.append((db, model_id))
        return "configured-client"

    monkeypatch.setattr("src.modules.assistant.service.get_configured_failover_client", fake_get_configured)
    conversation = service.create_conversation(CreateConversationCommand())

    assert service.build_context_compression_client() == "configured-client"
    assert calls[0][0] is session
    assert calls[0][1] == 9
    session.close()
    engine.dispose()


def test_service_prepares_context_and_persists_snapshot_without_mutating_messages(monkeypatch):
    from src.modules.assistant.schemas import CreateConversationCommand

    engine, session, service = _service(_settings())
    conversation = service.create_conversation(CreateConversationCommand(initial_context="详情页上下文"))
    for index in range(3):
        service.record_user_message(conversation.id, f"目标和约束 {index} " + "x" * 500)
    before = [message.content for message in service._repository.list_messages(conversation.id)]

    class FakeClient:
        async def chat_multi(self, messages, temperature=0.4):
            return '{"goal":["保留目标"],"constraints":["保留约束"],"decisions":[],"facts":[],"current_state":"继续","open_items":["下一步"],"tool_findings":[]}'

    monkeypatch.setattr(service, "build_context_compression_client", lambda: FakeClient())
    result = asyncio.run(service.prepare_context(conversation.id))

    assert result.compressed is True
    assert result.summary.goal == ["保留目标"]
    snapshot = service._repository.get_latest_context_snapshot(conversation.id)
    assert snapshot is not None
    assert snapshot.version == 1
    assert snapshot.usage_before["total_tokens"] > snapshot.usage_after["total_tokens"]
    assert [message.content for message in service._repository.list_messages(conversation.id)] == before
    session.close()
    engine.dispose()


def test_model_failure_still_creates_extractive_snapshot(monkeypatch):
    from src.modules.assistant.schemas import CreateConversationCommand

    engine, session, service = _service(_settings())
    conversation = service.create_conversation(CreateConversationCommand())
    service.record_user_message(conversation.id, "目标是继续跟踪这个标的 " + "x" * 600)
    service.record_user_message(conversation.id, "当前需要保留未完成事项 " + "x" * 600)

    class BrokenClient:
        async def chat_multi(self, messages, temperature=0.4):
            raise RuntimeError("compression provider down")

    monkeypatch.setattr(service, "build_context_compression_client", lambda: BrokenClient())
    result = asyncio.run(service.prepare_context(conversation.id))

    assert result.compressed is True
    assert result.summary is not None
    assert service._repository.get_latest_context_snapshot(conversation.id) is not None
    session.close()
    engine.dispose()


def test_service_exposes_context_usage_and_latest_summary_for_the_ui():
    from src.modules.assistant.schemas import CreateConversationCommand

    engine, session, service = _service(_settings())
    conversation = service.create_conversation(CreateConversationCommand())
    service.record_user_message(conversation.id, "当前问题")

    detail = service.get_context_detail(conversation.id)

    assert detail.conversation_id == conversation.id
    assert detail.usage.total_tokens > 0
    assert detail.snapshot is None
    assert detail.status == detail.usage.state
    session.close()
    engine.dispose()
