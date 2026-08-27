"""SSE 基建单测：事件编码 / 流缓冲续推 / 中间件直通。"""

import asyncio
import json

from src.core.sse import SSEHub, SSEStream, format_sse_event
from src.web.response import ResponseWrapperMiddleware


def test_format_sse_event():
    """SSE 事件编码：带 id/event/data，dict 自动 JSON 化"""
    text = format_sse_event(3, "token", {"text": "你好"})
    assert "id: 3\n" in text
    assert "event: token\n" in text
    assert 'data: {"text": "你好"}\n' in text
    assert text.endswith("\n\n")


def test_format_sse_event_multiline():
    """SSE 事件编码：data 含换行时拆成多个 data: 行"""
    text = format_sse_event(1, "token", "a\nb")
    assert "data: a\ndata: b\n" in text


def test_stream_replay_and_resume():
    """事件流：先发布后订阅可重放全部事件；带 after_seq 只续推之后的"""

    async def run():
        stream = SSEStream()
        await stream.publish("token", {"text": "a"})
        await stream.publish("token", {"text": "b"})
        await stream.publish("done", {})
        await stream.finish()

        # 从头订阅：3 条全收到
        all_events = [ev async for ev in stream.subscribe(after_seq=0)]
        assert len(all_events) == 3
        assert "id: 1\n" in all_events[0]

        # 断线重连：Last-Event-ID=2 → 只收到第 3 条
        resumed = [ev async for ev in stream.subscribe(after_seq=2)]
        assert len(resumed) == 1
        assert "id: 3\n" in resumed[0]
        assert "event: done\n" in resumed[0]

    asyncio.run(run())


def test_stream_live_subscribe():
    """事件流：订阅者阻塞等待，生产者发布后立即收到，finish 后退出"""

    async def run():
        stream = SSEStream()
        received: list[str] = []

        async def consumer():
            async for ev in stream.subscribe(after_seq=0):
                received.append(ev)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)
        await stream.publish("token", {"text": "hi"})
        await asyncio.sleep(0.01)
        await stream.finish()
        await asyncio.wait_for(task, timeout=2)
        assert len(received) == 1
        assert "event: token\n" in received[0]

    asyncio.run(run())


def test_hub_create_get_prune():
    """Hub：create/get 正常，超 TTL 的流被清理"""
    hub = SSEHub(ttl_sec=0.0)  # TTL=0 → 下次 prune 即清理
    stream = hub.create()
    # TTL 为 0，get 时触发 prune 已经清掉
    assert hub.get(stream.stream_id) is None

    hub2 = SSEHub(ttl_sec=60)
    s2 = hub2.create()
    assert hub2.get(s2.stream_id) is s2


def _make_scope(path="/api/chat/x"):
    return {"type": "http", "path": path}


def test_middleware_sse_passthrough():
    """中间件：text/event-stream 响应逐块直通，不缓冲"""

    async def run():
        sent_during_app: list[int] = []

        async def app(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream; charset=utf-8")],
            })
            await send({"type": "http.response.body", "body": b"id: 1\n\n", "more_body": True})
            # 记录此刻下游已收到多少条消息——直通模式下应该已实时转发
            sent_during_app.append(len(sent_messages))
            await send({"type": "http.response.body", "body": b"id: 2\n\n", "more_body": False})

        sent_messages: list[dict] = []

        async def send(message):
            sent_messages.append(message)

        mw = ResponseWrapperMiddleware(app)
        await mw(_make_scope(), None, send)

        # app 发送第二块前，start + 第一块已经转发到下游（证明未缓冲）
        assert sent_during_app == [2]
        assert len(sent_messages) == 3
        assert sent_messages[1]["body"] == b"id: 1\n\n"

    asyncio.run(run())


def test_middleware_json_still_wrapped():
    """中间件：普通 JSON 响应仍被包装为 {code, success, data, message}"""

    async def run():
        async def app(scope, receive, send):
            body = json.dumps({"hello": "world"}).encode()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": body})

        sent_messages: list[dict] = []

        async def send(message):
            sent_messages.append(message)

        mw = ResponseWrapperMiddleware(app)
        await mw(_make_scope(), None, send)

        body = json.loads(sent_messages[-1]["body"])
        assert body["code"] == 0
        assert body["success"] is True
        assert body["data"] == {"hello": "world"}

    asyncio.run(run())
