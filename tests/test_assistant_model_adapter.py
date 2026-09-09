"""Model adaptation stays in the PanWatch host, outside pan_agent."""

import asyncio
from types import SimpleNamespace

from pan_agent import ModelMessage, ToolRisk, ToolSpec
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import ChatConversation  # noqa: F401 - registers metadata


def test_failover_model_adapter_maps_tool_calls_and_streams_text():
    from src.modules.assistant.llm_adapter import FailoverModelAdapter

    class FakeClient:
        async def chat_with_tools(self, messages, tools, temperature):
            assert messages[0]["role"] == "user"
            assert tools[0]["function"]["name"] == "get_portfolio"
            assert temperature == 0.5
            return SimpleNamespace(
                content="已查询",
                tool_calls=[SimpleNamespace(id="call-1", function=SimpleNamespace(name="get_portfolio", arguments="{}"))],
            )

    emitted: list[str] = []

    async def emit(token: str) -> None:
        emitted.append(token)

    turn = asyncio.run(FailoverModelAdapter(FakeClient()).run_turn(
        [ModelMessage(role="user", content="我的持仓")],
        [ToolSpec(name="get_portfolio", title="持仓", description="查询持仓", risk=ToolRisk.READ,
                  input_schema={"type": "object", "properties": {}})],
        emit,
    ))

    assert turn.content == "已查询"
    assert turn.tool_calls[0].name == "get_portfolio"
    assert emitted == ["已查询"]


def test_assistant_service_builds_panagent_runtime_from_host_adapters():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.service import AssistantService

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    runtime = AssistantService(AssistantRepository(session)).build_runtime(object())

    assert runtime.__class__.__name__ == "AgentRuntime"
    session.close()
