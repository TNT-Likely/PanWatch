"""End-to-end approval coverage for the host-owned price-alert write tool."""

import asyncio

from pan_agent import (
    AgentRuntime,
    ApprovalDecision,
    ModelMessage,
    ModelTurn,
    RunRequest,
    RunStatus,
    ToolCall,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.assistant.repository import AssistantRepository
from src.modules.assistant.service import AssistantService
from src.modules.assistant.tools import build_panwatch_tool_registry
from src.platform.persistence.database import Base
from src.platform.persistence.models import PriceAlertRule, Stock


class _CollectingSink:
    async def publish(self, _event) -> None:
        pass


class _FixedModel:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self._turns = iter(turns)

    async def run_turn(self, _messages, _tools, _emit_token) -> ModelTurn:
        return next(self._turns)


def _setup():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Stock(symbol="600519", name="贵州茅台", market="CN"))
    session.commit()
    repository = AssistantRepository(session)
    conversation = repository.create_conversation(
        stock_symbol="600519", stock_market="CN", initial_context=None
    )
    task = repository.create_task(
        conversation_id=conversation.id, user_message_id=None, context={}
    )
    return engine, session, AssistantService(repository), task


def _request(task_id: int) -> RunRequest:
    return RunRequest(
        run_id=str(task_id),
        messages=[ModelMessage(role="user", content="贵州茅台涨到 1800 元时提醒我")],
    )


def _proposed_price_alert(call_id: str) -> ModelTurn:
    return ModelTurn(
        tool_calls=[
            ToolCall(
                id=call_id,
                name="create_price_alert",
                arguments={
                    "symbol": "600519",
                    "market": "CN",
                    "direction": "above",
                    "target_price": 1800,
                    "cooldown_minutes": 30,
                },
            )
        ]
    )


def test_price_alert_is_written_only_after_the_durable_approval_is_accepted():
    engine, session, service, task = _setup()
    request = _request(task.id)
    paused = asyncio.run(
        AgentRuntime(
            _FixedModel([_proposed_price_alert("price-alert")]),
            build_panwatch_tool_registry(session),
            policy=service.build_tool_policy(),
        ).run(request, _CollectingSink())
    )

    assert paused.status is RunStatus.WAITING_FOR_APPROVAL
    assert session.query(PriceAlertRule).count() == 0
    approvals = service.pause_task(task.id, paused)
    outcome = service.resolve_approval_decision(
        approvals[0].id, ApprovalDecision.APPROVED
    )

    resumed = asyncio.run(
        AgentRuntime(
            _FixedModel([ModelTurn(content="价格提醒已创建。")]),
            build_panwatch_tool_registry(session),
            policy=service.build_tool_policy(),
        ).resume(request, outcome.checkpoint, outcome.decisions, _CollectingSink())
    )

    assert resumed.status is RunStatus.COMPLETED
    assert session.query(PriceAlertRule).count() == 1
    session.close()
    engine.dispose()


def test_rejected_price_alert_never_writes_a_rule():
    engine, session, service, task = _setup()
    request = _request(task.id)
    paused = asyncio.run(
        AgentRuntime(
            _FixedModel([_proposed_price_alert("price-alert")]),
            build_panwatch_tool_registry(session),
            policy=service.build_tool_policy(),
        ).run(request, _CollectingSink())
    )

    approvals = service.pause_task(task.id, paused)
    outcome = service.resolve_approval_decision(
        approvals[0].id, ApprovalDecision.REJECTED
    )
    resumed = asyncio.run(
        AgentRuntime(
            _FixedModel([ModelTurn(content="已取消创建价格提醒。")]),
            build_panwatch_tool_registry(session),
            policy=service.build_tool_policy(),
        ).resume(request, outcome.checkpoint, outcome.decisions, _CollectingSink())
    )

    assert resumed.status is RunStatus.COMPLETED
    assert session.query(PriceAlertRule).count() == 0
    session.close()
    engine.dispose()
