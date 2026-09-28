"""Trusted assistant results remain deterministic and restorable."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import ChatConversation  # noqa: F401
from src.platform.tasking.contracts import TaskEventType, TaskStatus


def _invocation(
    tool_name: str,
    *,
    call_id: str,
    data: dict,
    arguments: dict | None = None,
    sources: list[dict] | None = None,
    observed_at: datetime | None = None,
    summary: str = "查询完成",
):
    return SimpleNamespace(
        call_id=call_id,
        tool_name=tool_name,
        status="completed",
        summary=summary,
        arguments=arguments or {},
        result_data=data,
        source_data=sources or [],
        observed_at=observed_at or datetime.now(UTC),
    )


def test_deterministic_result_uses_fields_and_flags_timepoint_discrepancies():
    from src.modules.assistant.result_builder import build_deterministic_assistant_result

    observed = datetime.now(UTC)
    result = build_deterministic_assistant_result(
        task_id=7,
        answer="结论。",
        language="zh-CN",
        invocations=[
            _invocation(
                "get_stock_quote",
                call_id="quote-1",
                data={"symbol": "600519", "market": "CN", "current_price": 105, "change_pct": 2.5},
                arguments={"symbol": "600519", "market": "CN"},
                sources=[{"name": "行情", "as_of": observed.isoformat()}],
                observed_at=observed,
            ),
            _invocation(
                "get_kline_summary",
                call_id="kline-1",
                data={"last_close": 100, "trend": "多头排列", "asof": "2026-09-28"},
                arguments={"symbol": "600519", "market": "CN"},
                sources=[{"name": "K 线", "as_of": "2026-09-28", "period_start": "2026-06-01", "period_end": "2026-09-28"}],
                observed_at=observed,
            ),
        ],
    )

    assert any("最新价为 105" in fact.text for fact in result.facts)
    assert any("收盘价 100" in fact.text and "多头排列" in fact.text for fact in result.facts)
    assert any("时点不同" in risk for risk in result.risks)
    assert any(
        action.kind == "navigate"
        and action.payload["path"] == "/portfolio?view=kline&symbol=600519&market=CN"
        for action in result.next_actions
    )


def test_deterministic_result_reports_missing_fields_and_stale_evidence():
    from src.modules.assistant.result_builder import build_deterministic_assistant_result

    result = build_deterministic_assistant_result(
        task_id=8,
        answer="未取得完整数据。",
        language="zh-CN",
        invocations=[
            _invocation(
                "get_stock_quote",
                call_id="quote-1",
                data={"symbol": "00700", "market": "HK", "current_price": None},
                arguments={"symbol": "00700", "market": "HK"},
                sources=[{"name": "行情", "as_of": (datetime.now(UTC) - timedelta(days=2)).isoformat()}],
            ),
            _invocation(
                "get_stock_news",
                call_id="news-1",
                data={"symbol": "00700", "market": "HK", "items": []},
                arguments={"symbol": "00700", "market": "HK"},
                sources=[{"name": "新闻"}],
            ),
        ],
    )

    assert any("缺少最新价" in item for item in result.missing_data)
    assert any("未检索到新闻" in item for item in result.missing_data)
    assert any("已经过期" in item for item in result.risks)


def test_conversation_restores_legacy_result_and_trace_without_answer_payload():
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
    task = repository.create_task(
        conversation_id=conversation.id, user_message_id=None, context={}
    )
    repository.claim_task(task.id)
    repository.append_task_event(
        task.id,
        TaskEventType.CONTEXT_PREPARED,
        status=TaskStatus.RUNNING,
        data={"compressed": False},
    )
    repository.record_tool_started(
        task.id,
        call_id="quote-1",
        tool_name="get_stock_quote",
        arguments={"symbol": "600519", "market": "CN"},
    )
    repository.record_tool_completed(
        task.id,
        call_id="quote-1",
        tool_name="get_stock_quote",
        summary="贵州茅台最新价 100。",
        result_data={},
        sources=[{"name": "历史行情"}],
        observed_at=datetime.now(UTC),
    )
    message = repository.complete_task_with_message(
        task.id, conversation.id, "历史结论。", result_data=None
    )
    assert message is not None

    service = AssistantService(repository)
    service._report_language = lambda: "zh-CN"  # type: ignore[method-assign]
    restored = service.get_conversation(conversation.id)
    assistant_message = next(item for item in restored.messages if item.role == "assistant")

    assert assistant_message.result is not None
    assert assistant_message.result.facts[0].text == "贵州茅台最新价 100。"
    assert assistant_message.trace is not None
    assert [item["event"] for item in assistant_message.trace] == [
        "run_started",
        "context_prepared",
        "tool_call_start",
        "tool_result",
        "done",
    ]
    done = assistant_message.trace[-1]["data"]
    assert "content" not in done and "result" not in done

    invocation = repository.list_task_tool_invocations(task.id)[0]
    assert invocation.result_data == {}
    session.close()
    engine.dispose()
