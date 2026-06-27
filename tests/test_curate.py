"""今日必读 AI 策展(Phase C)。"""
from __future__ import annotations
import asyncio
from src.web.api import dashboard


class _FakeAI:
    def __init__(self, r): self._r = r
    async def chat(self, s, u, temperature=0.3): return self._r


def test_curate_orders_by_importance(monkeypatch, db):
    """AI 返回的重要度用于降序排序,并带 why。"""
    monkeypatch.setattr(dashboard, "_get_ai_client", lambda db, mid=None: _FakeAI("0|40|小异动\n1|90|提醒触发"))
    req = dashboard.CurateRequest(candidates=[
        dashboard.CurateCandidate(type="watch", symbol="A", name="甲", signal="x"),
        dashboard.CurateCandidate(type="alert", symbol="B", name="乙", signal="y"),
    ])
    res = asyncio.run(dashboard.curate_today(req, db))
    assert res["items"][0]["index"] == 1
    assert res["items"][0]["importance"] == 90
    assert res["items"][0]["why"] == "提醒触发"


def test_curate_fallback_on_ai_fail(monkeypatch, db):
    """AI 失败时按原序兜底,不报错。"""
    class _Boom:
        async def chat(self, *a, **k): raise RuntimeError("boom")
    monkeypatch.setattr(dashboard, "_get_ai_client", lambda db, mid=None: _Boom())
    req = dashboard.CurateRequest(candidates=[dashboard.CurateCandidate(type="alert", symbol="B", name="乙", signal="y")])
    res = asyncio.run(dashboard.curate_today(req, db))
    assert len(res["items"]) == 1
    assert res["items"][0]["index"] == 0
