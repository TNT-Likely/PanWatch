"""应用关闭时的统一协调：标记关闭态、停止后台 worker、关闭调度器。"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

_shutting_down = False

P = ParamSpec("P")
T = TypeVar("T")


def mark_shutting_down() -> None:
    global _shutting_down
    _shutting_down = True


def is_shutting_down() -> bool:
    return _shutting_down


def reset_shutdown_state() -> None:
    """仅供测试重置全局关闭标记。"""
    global _shutting_down
    _shutting_down = False


def raise_if_shutting_down() -> None:
    """长任务在阶段间隙调用；关闭中则主动取消当前协程。"""
    if _shutting_down:
        raise asyncio.CancelledError()


def scheduler_job(
    func: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T | None]]:
    """调度器协程任务装饰器：关闭时静默退出，避免 APScheduler 打 ERROR。"""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | None:
        if is_shutting_down():
            return None
        try:
            return await func(*args, **kwargs)
        except asyncio.CancelledError:
            if is_shutting_down():
                logger.debug("%s 因服务关闭被取消", func.__qualname__)
                return None
            raise

    return wrapper


def shutdown_async_scheduler(scheduler: Any, *, wait: bool = False) -> None:
    """安全关闭 APScheduler AsyncIOScheduler 实例。"""
    if scheduler is None:
        return
    try:
        if getattr(scheduler, "running", False):
            scheduler.shutdown(wait=wait)
    except Exception as exc:
        logger.debug("调度器关闭时出现可忽略异常: %s", exc)


def graceful_shutdown(
    *,
    agent_scheduler: Any = None,
    price_alert_scheduler: Any = None,
    paper_trading_scheduler: Any = None,
    context_maintenance_scheduler: Any = None,
) -> None:
    """有序停止后台任务，尽量在进程退出前收尾。"""
    mark_shutting_down()

    try:
        from src.core.lmd_auto_bootstrap import shutdown_lmd_bootstrap_worker

        shutdown_lmd_bootstrap_worker()
    except Exception as exc:
        logger.debug("LMD bootstrap worker 关闭跳过: %s", exc)

    for label, sched in (
        ("上下文维护", context_maintenance_scheduler),
        ("价格提醒", price_alert_scheduler),
        ("模拟盘", paper_trading_scheduler),
        ("Agent", agent_scheduler),
    ):
        if sched is None:
            continue
        try:
            sched.shutdown()
            logger.info("%s调度器已关闭", label)
        except Exception as exc:
            logger.debug("%s调度器关闭跳过: %s", label, exc)
