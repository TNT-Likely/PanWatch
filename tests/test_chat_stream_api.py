"""chat SSE 流式端点单测：事件序列 / 工具循环 / 降级 / 断线续推（全 mock，不发真实请求）。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.web.api.chat as chat_api
from src.core.sse import SSEStream
from src.web.database import Base
from src.web.models import ChatConversation, ChatMessage


def _make_session_factory():
    """内存 SQLite 会话工厂（StaticPool 保证同一连接）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _setup_conversation(session_factory, content="持仓怎么样"):
    """建一条对话 + 用户消息，返回 conversation_id。"""
    db = session_factory()
    conv = ChatConversation(title="test")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    db.add(ChatMessage(conversation_id=conv.id, role="user", content=content))
    db.commit()
    conv_id = conv.id
    db.close()
    return conv_id


class _FakeAIClient:
    """按预设脚本逐轮响应的假 AI 客户端。

    rounds 每项形如：
    - ("tokens", ["你", "好"])：本轮流式产出文本后结束（无工具调用）；
    - ("tools", [{"id","name","arguments"}, ...])：本轮要求调用工具；
    - "raise"：本轮流式调用直接抛异常（触发降级路径）。
    """

    def __init__(self, rounds, chat_multi_result="降级回答", chat_multi_raises=False):
        self._rounds = list(rounds)
        self._chat_multi_result = chat_multi_result
        self._chat_multi_raises = chat_multi_raises
        self.model = "fake-model"

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        assert self._rounds, "脚本轮次已用尽"
        round_spec = self._rounds.pop(0)
        if round_spec == "raise":
            raise RuntimeError("stream unsupported")
        kind, payload = round_spec
        if kind == "tokens":
            for t in payload:
                yield ("token", t)
            yield ("message", {"content": "".join(payload), "tool_calls": []})
        else:
            yield ("message", {"content": "", "tool_calls": payload})

    async def chat_multi(self, messages, temperature=0.4):
        if self._chat_multi_raises:
            raise RuntimeError("multi also failed")
        return self._chat_multi_result


def _run_task_and_collect(monkeypatch, session_factory, ai_client, conv_id):
    """跑 _run_chat_stream_task 并收集全部事件，返回 [(event, data_dict), ...]。"""
    monkeypatch.setattr(chat_api, "SessionLocal", session_factory)
    monkeypatch.setattr(chat_api, "_get_ai_client", lambda db, model_id=None: ai_client)

    async def run():
        stream = SSEStream()
        await chat_api._run_chat_stream_task(conv_id, stream)
        events = []
        async for raw in stream.subscribe(after_seq=0):
            lines = raw.strip().split("\n")
            event = next(l.split(": ", 1)[1] for l in lines if l.startswith("event: "))
            data_raw = "\n".join(l.split(": ", 1)[1] for l in lines if l.startswith("data: "))
            events.append((event, json.loads(data_raw)))
        return events

    return asyncio.run(run())


def test_stream_task_plain_answer(monkeypatch):
    """无工具调用：token 事件逐个下发，done 携带完整回答且已落库"""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    ai = _FakeAIClient([("tokens", ["你", "好"])])

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["token", "token", "done"]
    assert events[0][1]["text"] == "你"
    assert events[-1][1]["content"] == "你好"

    db = session_factory()
    saved = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant")
        .all()
    )
    db.close()
    assert len(saved) == 1
    assert saved[0].content == "你好"


def test_stream_task_tool_loop(monkeypatch):
    """工具循环：tool_call_start/tool_result 事件先推，最终回答走 token 流"""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory, content="茅台现在多少钱")
    ai = _FakeAIClient([
        ("tools", [{"id": "c1", "name": "get_stock_quote", "arguments": '{"symbol": "600519"}'}]),
        ("tokens", ["茅台 1700 元"]),
    ])
    fake_exec = AsyncMock(return_value="实时行情：贵州茅台 价格 1700")
    monkeypatch.setattr(chat_api, "_execute_tool", fake_exec)

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["tool_call_start", "tool_result", "token", "done"]
    assert events[0][1] == {"name": "get_stock_quote", "arguments": {"symbol": "600519"}}
    assert events[1][1]["ok"] is True
    assert "1700" in events[1][1]["preview"]
    assert events[-1][1]["content"] == "茅台 1700 元"
    # 工具名与参数确实传给了执行器
    fake_exec.assert_awaited_once()
    assert fake_exec.await_args.args[1] == "get_stock_quote"
    assert fake_exec.await_args.args[2] == {"symbol": "600519"}


def test_stream_task_fallback_to_chat_multi(monkeypatch):
    """流式不可用：降级 chat_multi，整段文本作为一个 token 事件下发并落库"""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    ai = _FakeAIClient(["raise"], chat_multi_result="降级回答")

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["token", "done"]
    assert events[0][1]["text"] == "降级回答"
    assert events[-1][1]["content"] == "降级回答"


def test_stream_task_error_event(monkeypatch):
    """AI 彻底不可用：推 error 事件，错误文案照常落库（与非流式行为一致）"""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    ai = _FakeAIClient(["raise"], chat_multi_raises=True)

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["error", "done"]
    assert "multi also failed" in events[0][1]["message"]
    assert "AI 服务暂时不可用" in events[-1][1]["content"]

    db = session_factory()
    saved = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant")
        .first()
    )
    db.close()
    assert "AI 服务暂时不可用" in saved.content


def test_send_message_stream_endpoint(monkeypatch):
    """流式端点：保存用户消息，首条 meta 事件带 stream_id，可通过 hub 续推"""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    monkeypatch.setattr(chat_api, "SessionLocal", session_factory)

    async def fake_task(conversation_id, stream):
        await stream.publish("done", {"message_id": 1, "content": "x"})
        await stream.finish()

    monkeypatch.setattr(chat_api, "_run_chat_stream_task", fake_task)

    async def run():
        resp = await chat_api.send_message_stream(
            conv_id, chat_api.SendMessageBody(content="第二个问题")
        )
        assert resp.media_type == "text/event-stream"
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return "".join(chunks)

    body = asyncio.run(run())
    assert "event: meta\n" in body
    assert "event: done\n" in body

    # meta 里的 stream_id 能从 hub 找回（断线重连的依据）
    meta_line = next(
        l for l in body.split("\n") if l.startswith("data: ") and "stream_id" in l
    )
    stream_id = json.loads(meta_line[len("data: "):])["stream_id"]
    assert chat_api.chat_stream_hub.get(stream_id) is not None

    # 用户消息已落库
    db = session_factory()
    user_msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "user")
        .all()
    )
    db.close()
    assert any(m.content == "第二个问题" for m in user_msgs)


def test_resume_stream_not_found():
    """断线重连：未知/过期 stream_id 返回 404"""
    request = SimpleNamespace(headers={})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat_api.resume_message_stream("nonexistent", request, 0))
    assert ei.value.status_code == 404


def test_resume_stream_last_event_id(monkeypatch):
    """断线重连：Last-Event-ID header 优先于 query 参数，从其后续推"""
    async def run():
        stream = chat_api.chat_stream_hub.create()
        await stream.publish("token", {"text": "a"})
        await stream.publish("token", {"text": "b"})
        await stream.finish()

        request = SimpleNamespace(headers={"last-event-id": "1"})
        resp = await chat_api.resume_message_stream(stream.stream_id, request, 0)
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return "".join(chunks)

    body = asyncio.run(run())
    assert "id: 1\n" not in body
    assert "id: 2\n" in body
