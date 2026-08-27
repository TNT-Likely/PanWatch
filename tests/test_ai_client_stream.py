"""AIClient.chat_stream 流式通道单测（全部 mock，不发真实请求）。"""

import asyncio
from types import SimpleNamespace

from src.core.ai_client import AIClient


def _chunk(content=None, tool_calls=None, usage=None):
    """构造一个 OpenAI 流式 chunk 的最小替身。"""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice], usage=usage)


def _tc_delta(index, id="", name="", arguments=""):
    """构造一个 tool_call 增量分片。"""
    fn = SimpleNamespace(name=name or None, arguments=arguments or None)
    return SimpleNamespace(index=index, id=id or None, function=fn)


class _FakeStream:
    """模拟 AsyncOpenAI 的流式响应（异步迭代器）。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _make_client(chunks):
    """构造一个 completions.create 返回固定 chunk 序列的 AIClient。"""
    client = AIClient(base_url="http://mock", api_key="mock", model="mock-model")

    async def fake_create(**kwargs):
        assert kwargs.get("stream") is True
        return _FakeStream(chunks)

    client.client.chat.completions.create = fake_create
    return client


def _collect(client, **kwargs):
    """收集 chat_stream 产出的全部事件。"""

    async def run():
        events = []
        async for ev in client.chat_stream([{"role": "user", "content": "hi"}], **kwargs):
            events.append(ev)
        return events

    return asyncio.run(run())


def test_stream_tokens():
    """纯文本流：逐 token 产出，最后产出完整 message"""
    client = _make_client([
        _chunk(content="你"),
        _chunk(content="好"),
        _chunk(content="！"),
    ])
    events = _collect(client)
    tokens = [t for kind, t in events if kind == "token"]
    assert tokens == ["你", "好", "！"]
    kind, msg = events[-1]
    assert kind == "message"
    assert msg["content"] == "你好！"
    assert msg["tool_calls"] == []


def test_stream_tool_calls_assembled():
    """工具调用流：分片的 arguments 按 index 聚合成完整 tool_calls"""
    client = _make_client([
        _chunk(tool_calls=[_tc_delta(0, id="call_1", name="get_stock_quote")]),
        _chunk(tool_calls=[_tc_delta(0, arguments='{"symbol"')]),
        _chunk(tool_calls=[_tc_delta(0, arguments=': "600519"}')]),
        _chunk(tool_calls=[_tc_delta(1, id="call_2", name="get_portfolio", arguments="{}")]),
    ])
    events = _collect(client, tools=[{"type": "function", "function": {"name": "x"}}])
    kind, msg = events[-1]
    assert kind == "message"
    assert msg["content"] == ""
    assert msg["tool_calls"] == [
        {"id": "call_1", "name": "get_stock_quote", "arguments": '{"symbol": "600519"}'},
        {"id": "call_2", "name": "get_portfolio", "arguments": "{}"},
    ]


def test_stream_usage_and_empty_choices():
    """末尾只含 usage 的空 choices chunk 不报错，且累计 token 用量"""
    usage = SimpleNamespace(total_tokens=42)
    client = _make_client([
        _chunk(content="ok"),
        SimpleNamespace(choices=[], usage=usage),
    ])
    events = _collect(client)
    assert events[-1][1]["content"] == "ok"
    assert client.total_tokens_used == 42
