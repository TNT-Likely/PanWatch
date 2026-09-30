"""Background task discovery and transactional, durable inbox behavior."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import NotificationEvent
from src.platform.tasking.contracts import TaskEventType, TaskStatus
from src.modules.assistant.repository import AssistantRepository
from src.modules.assistant.service import AssistantService


def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repository = AssistantRepository(session)
    conversation = repository.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
    conversation.title = "后台研究"
    session.commit()
    task = repository.create_task(conversation_id=conversation.id, user_message_id=None, context={})
    return engine, session, repository, task


def test_activity_discovers_active_tasks_without_tokens_or_context():
    engine, session, repository, task = setup()
    repository.claim_task(task.id)
    repository.append_task_event(task.id, TaskEventType.STEP_PROGRESS, step_index=2)
    activity = AssistantService(repository).get_activity()
    assert len(activity.active_tasks) == 1
    assert activity.active_tasks[0].id == task.id
    assert activity.active_tasks[0].title == "后台研究"
    assert activity.active_tasks[0].current_step == 2
    assert "context" not in activity.active_tasks[0].model_dump()
    assert activity.notifications == []
    session.close()
    engine.dispose()


def test_completion_notification_survives_a_fresh_session_and_is_idempotent():
    engine, session, repository, task = setup()
    repository.claim_task(task.id)
    message = repository.complete_task_with_message(task.id, task.conversation_id, "完成")
    repository.finish_task(task.id, status="completed", final_message_id=message.id)
    task_id = task.id
    session.close()
    fresh = sessionmaker(bind=engine)()
    activity = AssistantRepository(fresh).get_activity()
    assert activity["active_tasks"] == []
    assert activity["unread_count"] == 1
    assert len(activity["notifications"]) == 1
    assert activity["notifications"][0]["task_id"] == task_id
    assert activity["notifications"][0]["kind"] == "completed"
    assert activity["notifications"][0]["created_at"].tzinfo is not None
    fresh.close()
    engine.dispose()


def test_notification_is_committed_with_its_event():
    engine, session, repository, task = setup()
    repository.append_task_event(task.id, TaskEventType.TASK_FAILED, status=TaskStatus.FAILED, commit=False)
    session.flush()
    assert session.query(NotificationEvent).count() == 1
    session.rollback()
    assert session.query(NotificationEvent).count() == 0
    assert len(repository.list_task_events(task.id)) == 2
    session.close()
    engine.dispose()


def test_existing_waiting_approvals_are_discovered_once_after_inbox_deployment():
    engine, session, repository, task = setup()
    task.status = TaskStatus.WAITING_APPROVAL.value
    session.commit()
    repository.restore_waiting_notifications()
    first = repository.get_activity()["notifications"][0]
    repository.read_notifications(ids=[first["id"]])
    repository.restore_waiting_notifications()
    assert len(repository.get_activity()["notifications"]) == 1
    assert repository.get_activity()["unread_count"] == 0
    session.close()
    engine.dispose()


def test_approval_notification_is_superseded_on_resume_and_cleared_on_stop():
    engine, session, repository, task = setup()
    repository.append_task_event(task.id, TaskEventType.TASK_PAUSED, status=TaskStatus.WAITING_APPROVAL)
    assert repository.get_activity()["notifications"][0]["kind"] == "awaiting_approval"
    repository.append_task_event(task.id, TaskEventType.TASK_STARTED, status=TaskStatus.RUNNING)
    assert repository.get_activity()["unread_count"] == 0
    assert repository.get_activity()["notifications"] == []
    repository.append_task_event(task.id, TaskEventType.TASK_PAUSED, status=TaskStatus.WAITING_APPROVAL)
    assert repository.get_activity()["unread_count"] == 1
    repository.cancel_task(task.id)
    assert repository.get_activity()["notifications"] == []
    session.close()
    engine.dispose()


def test_retry_resolves_the_old_failure_and_new_failure_notifies_once():
    engine, session, repository, task = setup()
    repository.finish_task(task.id, status="failed", final_message_id=None, error_code="run_timeout")
    original_id = repository.get_activity()["notifications"][0]["id"]
    repository.retry_task(task.id)
    assert repository.get_activity()["unread_count"] == 0
    repository.finish_task(task.id, status="failed", final_message_id=None, error_code="run_timeout")
    repository.finish_task(task.id, status="failed", final_message_id=None, error_code="run_timeout")
    activity = repository.get_activity()
    assert activity["unread_count"] == 1
    assert activity["notifications"][0]["id"] > original_id
    assert activity["notifications"][0]["kind"] == "failed"
    session.close()
    engine.dispose()


def test_mark_all_read_does_not_consume_a_notification_created_after_the_observed_cursor():
    engine, session, repository, task = setup()
    repository.finish_task(task.id, status="failed", final_message_id=None, error_code="run_timeout")
    cursor = repository.get_activity()["notification_cursor"]
    other = repository.create_task(conversation_id=task.conversation_id, user_message_id=None, context={})
    repository.finish_task(other.id, status="failed", final_message_id=None)
    assert repository.read_notifications(ids=[], through_id=cursor) == 1
    activity = repository.get_activity()
    assert activity["unread_count"] == 1
    assert activity["notifications"][0]["task_id"] == other.id
    assert activity["notifications"][0]["read_at"] is None
    new_id = activity["notifications"][0]["id"]
    assert repository.read_notifications(ids=[new_id]) == 1
    assert repository.read_notifications(ids=[new_id]) == 0
    assert repository.get_activity()["unread_count"] == 0
    session.close()
    engine.dispose()


def test_unread_notifications_are_not_hidden_behind_the_recent_read_history():
    engine, session, repository, task = setup()
    repository.finish_task(task.id, status="failed", final_message_id=None)
    old_id = repository.get_activity()["notifications"][0]["id"]
    for _ in range(32):
        other = repository.create_task(conversation_id=task.conversation_id, user_message_id=None, context={})
        repository.finish_task(other.id, status="failed", final_message_id=None)
        repository.read_notifications(ids=[repository.get_activity()["notification_cursor"]])
    activity = repository.get_activity()
    assert activity["notifications"][0]["id"] == old_id
    assert activity["unread_count"] == 1
    assert len(activity["notifications"]) == 30
    repository.delete_conversation(repository.get_conversation(task.conversation_id))
    assert repository.get_activity()["notifications"] == []
    session.close()
    engine.dispose()


def test_notification_migration_is_idempotent_and_enforces_event_uniqueness(tmp_path):
    from src.platform.persistence.migrations import _m129_assistant_task_notifications
    from sqlalchemy.exc import IntegrityError
    import pytest

    engine = create_engine(f"sqlite:///{tmp_path / 'notifications.db'}")
    with engine.begin() as connection:
        _m129_assistant_task_notifications(connection)
        _m129_assistant_task_notifications(connection)
        connection.execute(text("INSERT INTO assistant_task_notifications(task_run_id,event_sequence,kind) VALUES(1,3,'completed')"))
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO assistant_task_notifications(task_run_id,event_sequence,kind) VALUES(1,3,'completed')"))
        indexes = {row[1] for row in connection.execute(text("PRAGMA index_list(assistant_task_notifications)"))}
        assert 'ix_assistant_notification_inbox' in indexes
        assert 'ix_assistant_notification_task' in indexes
    engine.dispose()
