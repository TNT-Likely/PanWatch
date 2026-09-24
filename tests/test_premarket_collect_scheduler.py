"""盘后预采集调度单测:15:35 cron 注册 + 交易日守卫 + 失败退避重试(全部离线)。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.platform.scheduling import trading_calendar as tc


def test_15点35分预采集任务已注册(monkeypatch):
    """预采集 cron 以 15:35 注册在上下文维护调度器上,与既有 job 注册方式一致。"""
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    calls: list[dict] = []
    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")

    def _fake_add_job(fn, trigger, **kwargs):
        calls.append({"fn": fn, "trigger": trigger, **kwargs})

    monkeypatch.setattr(sched.scheduler, "add_job", _fake_add_job)
    monkeypatch.setattr(
        "src.platform.scheduling.scheduler_registry.register", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        sched.scheduler, "start", lambda: None
    )  # 不真正起事件循环调度
    sched.start()

    job = next(c for c in calls if c["id"] == "context_maintenance_premarket_sector_collect")
    assert job["trigger"] == "cron"
    assert job["hour"] == 15
    assert job["minute"] == 35
    assert job["max_instances"] == 1
    assert job["replace_existing"] is True
    assert asyncio.iscoroutinefunction(job["fn"])


def test_预采集任务非交易日跳过(monkeypatch):
    """A股休市日(周末)不采集。"""
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    calls = {"collect": 0}
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: datetime(2026, 8, 8, 15, 35, tzinfo=ZoneInfo("Asia/Shanghai")))

    import src.modules.market.sector_data_service as svc

    def _fake_collect(db, **kw):
        calls["collect"] += 1
        return {}

    monkeypatch.setattr(svc, "collect_daily_snapshot", _fake_collect)

    s = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(s._premarket_sector_collect_job())
    assert calls["collect"] == 0


def test_预采集任务交易日内执行且失败退避重试(monkeypatch):
    """交易日执行采集;前两次失败按指数退避重试,第三次成功不再休眠等待。"""
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: datetime(2026, 8, 10, 15, 35, tzinfo=ZoneInfo("Asia/Shanghai")))

    import src.modules.market.sector_data_service as svc

    state = {"attempts": 0, "sleeps": []}

    def _flaky_collect(db, **kw):
        state["attempts"] += 1
        if state["attempts"] < 3:
            raise RuntimeError("源暂时不可用")
        return {"upserted": 2, "degraded": True}

    def _fake_macro(db):
        return {"upserted": 1, "ok": True}

    monkeypatch.setattr(svc, "collect_daily_snapshot", _flaky_collect)
    monkeypatch.setattr(svc, "refresh_macro_cache", _fake_macro)

    async def _fake_sleep(delay):
        state["sleeps"].append(delay)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    s = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(s._premarket_sector_collect_job())

    assert state["attempts"] == 3
    assert state["sleeps"] == [2.0, 4.0]  # 指数退避


def test_预采集任务三次全败不抛错(monkeypatch):
    """连续 3 次失败:记录日志放弃本轮,不向调度器抛异常。"""
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: datetime(2026, 8, 10, 15, 35, tzinfo=ZoneInfo("Asia/Shanghai")))

    import src.modules.market.sector_data_service as svc

    state = {"attempts": 0}

    def _always_fail(db, **kw):
        state["attempts"] += 1
        raise RuntimeError("持续不可用")

    monkeypatch.setattr(svc, "collect_daily_snapshot", _always_fail)

    async def _fake_sleep(delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    s = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(s._premarket_sector_collect_job())  # 不应抛出
    assert state["attempts"] == 3
