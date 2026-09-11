"""Model adaptation stays in the PanWatch host, outside pan_agent."""

import asyncio

from pan_agent import ModelMessage, ToolCall, ToolRisk, ToolSpec
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    ChatConversation,  # noqa: F401 - registers metadata
)


def test_failover_model_adapter_forwards_each_model_stream_chunk_and_maps_tool_calls():
    from src.modules.assistant.llm_adapter import FailoverModelAdapter

    class FakeClient:
        async def chat_stream(self, messages, tools, temperature):
            assert messages[0]["role"] == "user"
            assert tools[0]["function"]["name"] == "get_portfolio"
            assert temperature == 0.5
            yield ("token", "已")
            yield ("token", "查询")
            yield ("message", {
                "content": "已查询",
                "tool_calls": [{"id": "call-1", "name": "get_portfolio", "arguments": "{}"}],
            })

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
    assert emitted == ["已", "查询"]


def test_failover_model_adapter_encodes_tool_call_history_for_model():
    from src.modules.assistant.llm_adapter import FailoverModelAdapter

    captured_messages: list[dict] = []

    class FakeClient:
        async def chat_stream(self, messages, tools, temperature):
            captured_messages.extend(messages)
            yield ("message", {"content": "已基于持仓回答", "tool_calls": []})

    async def ignore_token(_token: str) -> None:
        return None

    turn = asyncio.run(FailoverModelAdapter(FakeClient()).run_turn(
        [
            ModelMessage(
                role="assistant",
                tool_calls=[ToolCall(id="call-1", name="get_portfolio", arguments={})],
            ),
            ModelMessage(
                role="tool",
                name="get_portfolio",
                tool_call_id="call-1",
                content="实盘持仓：广汽集团",
            ),
        ],
        [],
        ignore_token,
    ))

    assert turn.content == "已基于持仓回答"
    assert captured_messages == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "get_portfolio", "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "content": "实盘持仓：广汽集团",
            "tool_call_id": "call-1",
            "name": "get_portfolio",
        },
    ]


def test_assistant_service_builds_panagent_runtime_from_host_adapters():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.service import AssistantService

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    runtime = AssistantService(AssistantRepository(session)).build_runtime(object())

    assert runtime.__class__.__name__ == "AgentRuntime"
    session.close()
