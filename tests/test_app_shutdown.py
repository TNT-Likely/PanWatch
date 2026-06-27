"""应用关闭协调逻辑单测。"""

import asyncio

import pytest

from src.core import app_shutdown


@pytest.fixture(autouse=True)
def _reset_shutdown_flag():
    app_shutdown.reset_shutdown_state()
    yield
    app_shutdown.reset_shutdown_state()


def test_scheduler_job_在关闭态静默退出():
    """关闭标记为真时，调度任务应直接返回而不抛错。"""

    @app_shutdown.scheduler_job
    async def sample_job():
        return "done"

    app_shutdown.mark_shutting_down()
    assert asyncio.run(sample_job()) is None


def test_scheduler_job_关闭中取消不向外抛错():
    """服务关闭时 CancelledError 应被吞掉，避免 APScheduler 打 ERROR。"""

    @app_shutdown.scheduler_job
    async def sample_job():
        raise asyncio.CancelledError()

    app_shutdown.mark_shutting_down()
    assert asyncio.run(sample_job()) is None


def test_raise_if_shutting_down_会触发取消():
    """长任务阶段检查在关闭态应抛出 CancelledError。"""
    app_shutdown.mark_shutting_down()
    with pytest.raises(asyncio.CancelledError):
        app_shutdown.raise_if_shutting_down()
