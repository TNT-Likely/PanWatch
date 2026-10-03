"""HTTP control-plane contracts for durable assistant tasks."""

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base


def _service():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.service import AssistantService

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repository = AssistantRepository(session)
    conversation = repository.create_conversation(
        stock_symbol=None, stock_market=None, initial_context=None
    )
    return engine, session, repository, conversation, AssistantService(repository)


def test_create_task_endpoint_returns_before_worker_execution(monkeypatch):
    import src.modules.assistant.api as assistant_api

    engine, session, repository, conversation, service = _service()
    started: list[tuple[int, int]] = []
    monkeypatch.setattr(
        assistant_api.assistant_task_runner,
        "start_message",
        lambda task_id, conversation_id: started.append((task_id, conversation_id)),
    )

    async def run():
        return await assistant_api.create_assistant_task(
            conversation.id,
            assistant_api.SendAssistantMessageCommand(content="分析市场"),
            service,
        )

    response = asyncio.run(run())

    assert response["status"] == "queued"
    assert response["task_id"] == started[0][0]
    assert started[0][1] == conversation.id
    assert repository.get_task_snapshot(response["task_id"])["status"] == "queued"

    session.close()
    engine.dispose()


def test_cancel_and_retry_task_controls_are_idempotent(monkeypatch):
    import src.modules.assistant.api as assistant_api

    engine, session, repository, conversation, service = _service()
    task = repository.create_task(
        conversation_id=conversation.id, user_message_id=None, context={}
    )
    repository.finish_task(task.id, status="failed", final_message_id=None, error_code="x")
    started: list[int] = []
    monkeypatch.setattr(
        assistant_api.assistant_task_runner,
        "start_message",
        lambda task_id, _conversation_id: started.append(task_id),
    )

    async def run():
        retried = await assistant_api.retry_assistant_task(task.id, service)
        cancelled = await assistant_api.cancel_assistant_task(task.id, service)
        cancelled_again = await assistant_api.cancel_assistant_task(task.id, service)
        return retried, cancelled, cancelled_again

    retried, cancelled, cancelled_again = asyncio.run(run())

    assert retried["status"] == "queued"
    assert started == [task.id]
    assert cancelled["status"] == "cancelled"
    assert cancelled_again["status"] == "cancelled"
    # HTTP control writes use independent worker sessions. Refresh this
    # fixture's pre-existing identity map before reading the committed state.
    session.expire_all()
    assert repository.get_task_snapshot(task.id)["retry_count"] == 1

    session.close()
    engine.dispose()


def test_retry_waits_for_a_cancelling_worker_to_exit(monkeypatch):
    import src.modules.assistant.api as assistant_api

    engine, session, repository, conversation, service = _service()
    task = repository.create_task(conversation_id=conversation.id, user_message_id=None, context={})
    repository.cancel_task(task.id)
    started = []
    monkeypatch.setattr(assistant_api.assistant_task_runner, "is_running", lambda _id: True)
    monkeypatch.setattr(assistant_api.assistant_task_runner, "start_message", lambda *args: started.append(args))
    snapshot = asyncio.run(assistant_api.retry_assistant_task(task.id, service))
    assert snapshot["status"] == "cancelled"
    assert snapshot["can_retry"] is False
    assert snapshot["retry_blocked_reason"] == "worker_stopping"
    assert snapshot["retry_count"] == 0
    assert started == []
    monkeypatch.setattr(assistant_api.assistant_task_runner, "is_running", lambda _id: False)
    retried = asyncio.run(assistant_api.retry_assistant_task(task.id, service))
    assert retried["status"] == "queued"
    assert retried["retry_count"] == 1
    assert started == [(task.id, conversation.id)]
    session.close()
    engine.dispose()
