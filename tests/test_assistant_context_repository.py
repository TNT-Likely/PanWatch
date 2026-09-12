from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pan_agent import ContextCompressionMode, ContextSummary, ContextUsage
from src.platform.persistence.database import Base
from src.platform.persistence.models import ChatConversation, ChatMessage  # noqa: F401


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _usage(total: int) -> ContextUsage:
    return ContextUsage(
        total_tokens=total,
        budget_tokens=12000,
        soft_limit_tokens=8400,
        hard_limit_tokens=10200,
    )


def test_context_snapshot_versions_increment_and_keep_messages_unchanged():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.schemas import CreateConversationCommand
    from src.modules.assistant.service import AssistantService

    engine, session = _session()
    service = AssistantService(AssistantRepository(session))
    conversation = service.create_conversation(CreateConversationCommand())
    service.record_user_message(conversation.id, "保留这个目标")
    original = [row.content for row in service._repository.list_messages(conversation.id)]

    repo = service._repository
    first = repo.save_context_snapshot(
        conversation.id,
        mode=ContextCompressionMode.BALANCED,
        summary=ContextSummary(goal=["保留这个目标"]),
        covered_until_message_id=1,
        source_message_count=1,
        usage_before=_usage(9000),
        usage_after=_usage(2000),
    )
    second = repo.save_context_snapshot(
        conversation.id,
        mode=ContextCompressionMode.PRESERVE_DETAILS,
        summary=ContextSummary(goal=["保留这个目标", "继续分析"]),
        covered_until_message_id=2,
        source_message_count=2,
        usage_before=_usage(10000),
        usage_after=_usage(2500),
    )

    assert first.version == 1
    assert second.version == 2
    assert repo.get_latest_context_snapshot(conversation.id).version == 2
    assert [row.content for row in repo.list_messages(conversation.id)] == original
    assert len(repo.list_context_snapshots(conversation.id)) == 2
    session.close()
    engine.dispose()
