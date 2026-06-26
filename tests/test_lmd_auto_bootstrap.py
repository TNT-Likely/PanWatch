"""产业周期视角自动补全测试。"""

from types import SimpleNamespace

import pytest

from src.core import lmd_auto_bootstrap as mod


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, *, has_report: bool = False, enabled: bool = True):
        self._has_report = has_report
        self._enabled = enabled

    def query(self, model):
        model_key = str(model)
        if "AnalysisHistory" in model_key:
            return _FakeQuery(1 if self._has_report else None)
        if model_key.endswith("AgentConfig") or "AgentConfig" in model_key:
            agent = SimpleNamespace(
                config={
                    "auto_bootstrap": {
                        "enabled": self._enabled,
                        "suppress_notify": True,
                    }
                }
            )
            return _FakeQuery(agent)
        return _FakeQuery(None)

    def close(self):
        return None


def test_has_lmd_report_false():
    """无历史记录时应判定为尚未生成产业周期视角报告。"""
    db = _FakeSession(has_report=False)
    assert mod.has_lmd_report(db, "600519") is False


def test_has_lmd_report_true():
    """已有历史记录时应判定为已生成产业周期视角报告。"""
    db = _FakeSession(has_report=True)
    assert mod.has_lmd_report(db, "600519") is True


def test_ensure_skips_when_report_exists(monkeypatch):
    """已有报告时不应再次排队。"""
    monkeypatch.setattr(mod, "SessionLocal", lambda: _FakeSession(has_report=True))
    result = mod.ensure_lmd_report(SimpleNamespace(symbol="600519", name="茅台", market="CN", id=1))
    assert result["has_report"] is True
    assert result["queued"] is False


def test_ensure_queues_when_missing(monkeypatch):
    """缺失报告时应加入后台队列。"""
    monkeypatch.setattr(mod, "SessionLocal", lambda: _FakeSession(has_report=False))
    monkeypatch.setattr(mod, "_ensure_worker", lambda: None)
    queued_items: list[tuple] = []

    def _put(item):
        queued_items.append(item)

    monkeypatch.setattr(mod._task_queue, "put", _put)
    mod._in_flight.clear()

    result = mod.ensure_lmd_report(SimpleNamespace(symbol="600519", name="茅台", market="CN", id=1))
    assert result["queued"] is True
    assert len(queued_items) == 1
    mod._in_flight.clear()


def test_ensure_deduplicates_inflight(monkeypatch):
    """同一标的已在生成中时不重复排队。"""
    monkeypatch.setattr(mod, "SessionLocal", lambda: _FakeSession(has_report=False))
    mod._in_flight.add("600519")
    try:
        result = mod.ensure_lmd_report(SimpleNamespace(symbol="600519", name="茅台", market="CN", id=1))
        assert result["deduplicated"] is True
        assert result["queued"] is False
        assert "生成中" in result["message"]
    finally:
        mod._in_flight.discard("600519")


def test_try_acquire_lmd_generation():
    """生成槽位占用与释放应成对生效。"""
    mod._in_flight.discard("000001")
    assert mod.try_acquire_lmd_generation("000001") is True
    assert mod.is_lmd_in_flight("000001") is True
    assert mod.try_acquire_lmd_generation("000001") is False
    mod.release_lmd_generation("000001")
    assert mod.is_lmd_in_flight("000001") is False
