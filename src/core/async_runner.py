"""在独立线程/进程中安全运行协程,避免 httpx 清理与事件循环竞态。"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


async def close_ai_client(ai_client: Any) -> None:
    """关闭 Agent 上下文中的 AIClient(若支持)。"""
    if ai_client is None:
        return
    close = getattr(ai_client, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:
        pass


def run_async_isolated(coro: Coroutine[Any, Any, T]) -> T:
    """在线程内运行协程;优先 asyncio.run,已有 loop 时退化为独立 loop。"""
    from src.core.app_shutdown import is_shutting_down

    if is_shutting_down():
        raise RuntimeError("服务正在关闭，跳过异步任务")
    try:
        return asyncio.run(coro)
    except RuntimeError:
        if is_shutting_down():
            raise RuntimeError("服务正在关闭，跳过异步任务") from None
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)
