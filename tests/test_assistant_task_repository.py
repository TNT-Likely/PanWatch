"""Durable assistant task snapshots outlive the in-memory SSE buffer."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.web.database import Base
from src.web.models import ChatConversation  # noqa: F401 - registers metadata


def test_task_snapshot_contains_completed_tool_after_stream_expiry():
    from src.modules.assistant.repository import AssistantRepository

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repo = AssistantRepository(session)
    conversation = repo.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)

    task = repo.create_task(conversation_id=conversation.id, user_message_id=None, context={"sourcePage": "portfolio"})
    repo.record_tool_completed(task.id, call_id="call-1", tool_name="get_portfolio", summary="2 个持仓")
    repo.finish_task(task.id, status="completed", final_message_id=None)

    snapshot = repo.get_task_snapshot(task.id)
    assert snapshot["status"] == "completed"
    assert snapshot["tools"] == [{"call_id": "call-1", "tool": "get_portfolio", "status": "completed", "summary": "2 个持仓"}]
    session.close()


def test_service_exposes_durable_task_snapshot():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.service import AssistantService

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repo = AssistantRepository(session)
    conversation = repo.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
    task = repo.create_task(conversation_id=conversation.id, user_message_id=None, context={})

    assert AssistantService(repo).get_task_snapshot(task.id)["id"] == task.id
    session.close()
