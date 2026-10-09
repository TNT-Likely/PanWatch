"""The assistant module owns conversation use cases, not HTTP route helpers."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pan_agent import ModelMessage, RunRequest, ToolRisk, ToolSpec
import pytest

from src.platform.persistence.database import Base
from src.platform.persistence.models import ChatConversation, ChatMessage  # noqa: F401 - registers metadata


def test_assistant_service_creates_reads_and_deletes_conversation_history():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.schemas import CreateConversationCommand
    from src.modules.assistant.service import AssistantService

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    service = AssistantService(AssistantRepository(session))

    created = service.create_conversation(
        CreateConversationCommand(stock_symbol="600519", stock_market="CN", initial_context="来自个股页")
    )
    service.record_user_message(created.id, "帮我看看")

    detail = service.get_conversation(created.id)
    assert detail.conversation.stock_symbol == "600519"
    assert detail.messages[0].content == "帮我看看"

    service.delete_conversation(created.id)
    assert service.list_conversations() == []
    session.close()


def test_service_policy_hides_tools_outside_an_action_allowlist():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.service import AssistantService

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    policy = AssistantService(AssistantRepository(session)).build_tool_policy()
    write_tool = ToolSpec(
        name="create_alert",
        title="创建提醒",
        description="write",
        risk=ToolRisk.WRITE,
    )
    read_tool = ToolSpec(
        name="get_portfolio",
        title="查询持仓",
        description="read",
        risk=ToolRisk.READ,
    )
    request = RunRequest(
        run_id="action",
        messages=[ModelMessage(role="user", content="创建提醒")],
        context={"allowed_tool_names": ["create_alert"]},
    )

    assert policy.is_tool_visible(request, write_tool) is True
    assert policy.is_tool_visible(request, read_tool) is False
    session.close()
    engine.dispose()


def _watch_service():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.service import AssistantService
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repository = AssistantRepository(session)
    conversation = repository.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
    return engine, session, repository, conversation, AssistantService(repository)


@pytest.mark.parametrize("retry", ["再试一下", "再检查一次", "try again"])
def test_explicit_retry_keeps_original_user_conditions_before_context_compression(retry):
    engine, session, repository, conversation, service = _watch_service()
    original = "帮我设置 CN:600519 收盘站上20日均线且连续3天满足时提醒，不接受替换成盘中价格提醒。"
    message = service.record_user_message(conversation.id, original)
    first = service.create_task(conversation.id, message.id)
    repository.add_message(repository.get_conversation(conversation.id), role="assistant", content="错误历史说法：现在可以用 US:AAPL 的盘中价格替代。")
    continuation = service.record_user_message(conversation.id, retry)
    task = service.create_task(conversation.id, continuation.id)
    plan = task.context["watch_request"]
    assert plan["original_text"] == original
    assert plan["current_turn_text"] == retry
    assert plan["referenced_user_message_id"] == message.id
    assert plan["instruments"] == [{"market":"CN", "symbol":"600519"}]
    assert set(plan["unsupported_conditions"]) == {"bar_close_confirmation", "moving_average_trigger", "consecutive_sessions"}
    assert first.context["watch_request"]["original_text"] == original
    session.close(); engine.dispose()


def test_repeated_retry_preserves_saved_expiry_and_rechecks_old_capability_plan():
    engine, session, repository, conversation, service = _watch_service()
    original = "CN:600519 收盘高于999999时提醒，两周有效"
    message = service.record_user_message(conversation.id, original)
    task = service.create_task(conversation.id, message.id)
    plan = dict(task.context["watch_request"])
    plan["unsupported_conditions"] = []  # A persisted older parser snapshot.
    plan["horizon"] = {"days":14, "expire_at":"2026-10-20T01:23:45+00:00"}
    task.context = {**task.context, "watch_request":plan}
    session.commit()
    for text in ["再试一下", "再检查一次"]:
        retry = service.record_user_message(conversation.id, text)
        actual = service.create_task(conversation.id, retry.id).context["watch_request"]
        assert actual["original_text"] == original
        assert actual["horizon"] == plan["horizon"]
        assert "bar_close_confirmation" in actual["unsupported_conditions"]
    session.close(); engine.dispose()


def test_retry_does_not_cross_a_new_user_topic_or_inherit_an_assistant_rule():
    engine, session, repository, conversation, service = _watch_service()
    service.record_user_message(conversation.id, "CN:600519 收盘站上20日均线时提醒")
    service.record_user_message(conversation.id, "解释一下市盈率")
    repository.add_message(repository.get_conversation(conversation.id), role="assistant", content="帮我设置 US:AAPL 价格大于100的提醒")
    retry = service.record_user_message(conversation.id, "再试一下")
    plan = service.create_task(conversation.id, retry.id).context["watch_request"]
    assert plan["original_text"] == "再试一下"
    assert plan["requires_clarification"] == ["referenced_request_missing"]
    assert "referenced_user_message_id" not in plan
    session.close(); engine.dispose()


def test_new_request_with_changed_conditions_does_not_inherit_old_rejection():
    engine, session, repository, conversation, service = _watch_service()
    service.record_user_message(conversation.id, "CN:600519 收盘站上20日均线时提醒")
    text = "改成 US:AAPL 价格大于999999的盘中提醒"
    message = service.record_user_message(conversation.id, text)
    plan = service.create_task(conversation.id, message.id).context["watch_request"]
    assert plan["original_text"] == text and plan["unsupported_conditions"] == []
    assert plan["instruments"] == [{"market":"US", "symbol":"AAPL"}]
    session.close(); engine.dispose()


def test_legacy_retry_uses_its_message_boundary_even_after_a_new_topic():
    from src.modules.assistant.watch_request import inspect_watch_request
    engine, session, repository, conversation, service = _watch_service()
    original = "CN:600519 收盘站上20日均线且连续3天满足时提醒"
    source = service.record_user_message(conversation.id, original)
    service.create_task(conversation.id, source.id)
    retry = service.record_user_message(conversation.id, "再试一下")
    legacy_plan = inspect_watch_request("再试一下")
    task = repository.create_task(conversation_id=conversation.id, user_message_id=retry.id, context={"watch_request":legacy_plan})
    service.record_user_message(conversation.id, "解释一下市盈率")
    plan = service.refresh_legacy_watch_retry(task, legacy_plan)
    assert plan["original_text"] == original
    assert set(plan["unsupported_conditions"]) == {"bar_close_confirmation", "moving_average_trigger", "consecutive_sessions"}
    assert legacy_plan["original_text"] == "再试一下"  # Do not rewrite history.
    session.close(); engine.dispose()
