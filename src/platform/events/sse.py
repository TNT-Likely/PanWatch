"""SSE（Server-Sent Events）基础设施。

提供两块能力：
1. `format_sse_event`：把事件编码为 SSE wire 格式（带自增序号 id，供 Last-Event-ID 续推）。
2. `SSEStream` / `SSEHub`：生成过程与连接解耦的事件缓冲。
   - 生产者（后台任务）往 `SSEStream` publish 事件，与 HTTP 连接无关，断线不中断生成；
   - 消费者（SSE 端点）从任意序号开始 subscribe，断线重连带 Last-Event-ID 即可续推；
   - 流结束（finish）后仍保留一段时间（TTL），供迟到的重连读取完整事件。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

# 流结束后保留时长（秒）：足够前端断线重连拿到完整结果
STREAM_TTL_SEC = 600
# 单条流的事件数量上限（防御性兜底，防止异常任务撑爆内存）
MAX_EVENTS_PER_STREAM = 10000


def format_sse_event(seq: int, event: str, data: dict | str) -> str:
    """编码单条 SSE 事件（id + event + data，data 统一 JSON）。"""
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False)
    # data 含换行时按 SSE 协议拆成多个 data: 行
    data_lines = "".join(f"data: {line}\n" for line in data.split("\n"))
    return f"id: {seq}\nevent: {event}\n{data_lines}\n"


def format_sse_comment(text: str = "keepalive") -> str:
    """编码 SSE 注释行（心跳，防止代理断开空闲连接）。"""
    return f": {text}\n\n"


@dataclass
class _Event:
    seq: int
    event: str
    data: dict | str


@dataclass
class SSEStream:
    """一条可重放的事件流（生产端与消费端解耦）。"""

    stream_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.monotonic)
    done: bool = False

    def __post_init__(self):
        self._events: list[_Event] = []
        self._cond = asyncio.Condition()

    async def publish(self, event: str, data: dict | str) -> int:
        """追加一条事件，返回其序号（从 1 开始）。"""
        async with self._cond:
            if len(self._events) >= MAX_EVENTS_PER_STREAM:
                # 超限直接置为结束，避免无界增长
                self.done = True
                self._cond.notify_all()
                return len(self._events)
            seq = len(self._events) + 1
            self._events.append(_Event(seq=seq, event=event, data=data))
            self._cond.notify_all()
            return seq

    async def finish(self) -> None:
        """标记流结束（订阅者读完缓冲后自然退出）。"""
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    async def subscribe(self, after_seq: int = 0, heartbeat_sec: float = 15.0):
        """从 after_seq 之后开始消费事件（异步生成器，产出 SSE wire 格式字符串）。

        - 先重放缓冲中已存在的事件（断线重连 Last-Event-ID 续推的关键）；
        - 追平后阻塞等待新事件；等待超过 heartbeat_sec 则产出心跳注释；
        - 流 done 且缓冲读完后结束。
        """
        cursor = max(0, int(after_seq))
        while True:
            batch: list[_Event] = []
            async with self._cond:
                if cursor < len(self._events):
                    batch = self._events[cursor:]
                    cursor = len(self._events)
                elif self.done:
                    return
                else:
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=heartbeat_sec)
                    except asyncio.TimeoutError:
                        pass
            if batch:
                for ev in batch:
                    yield format_sse_event(ev.seq, ev.event, ev.data)
            else:
                async with self._cond:
                    idle = not (cursor < len(self._events) or self.done)
                if idle:
                    yield format_sse_comment()


class SSEHub:
    """按 stream_id 管理多条 SSEStream，带 TTL 清理。"""

    def __init__(self, ttl_sec: float = STREAM_TTL_SEC):
        self._streams: dict[str, SSEStream] = {}
        self._ttl_sec = ttl_sec

    def create(self) -> SSEStream:
        self._prune()
        stream = SSEStream()
        self._streams[stream.stream_id] = stream
        return stream

    def get(self, stream_id: str) -> SSEStream | None:
        self._prune()
        return self._streams.get(stream_id)

    def _prune(self) -> None:
        """清掉超过 TTL 的旧流。"""
        now = time.monotonic()
        expired = [
            sid for sid, s in self._streams.items()
            if now - s.created_at > self._ttl_sec
        ]
        for sid in expired:
            self._streams.pop(sid, None)


# chat 对话流的全局 hub（进程内单例；生成任务与 SSE 连接通过它解耦）
chat_stream_hub = SSEHub()
