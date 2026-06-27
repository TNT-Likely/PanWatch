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
    def __init__(
        self,
        *,
        has_report: bool = False,
        enabled: bool = True,
        scan_on_startup: bool = False,
    ):
        self._has_report = has_report
        self._enabled = enabled
        self._scan_on_startup = scan_on_startup

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
                        "scan_on_startup": self._scan_on_startup,
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


def test_bootstrap_skips_when_startup_scan_disabled(monkeypatch):
    """启动全量扫描关闭时不应排队。"""
    monkeypatch.setattr(mod, "ensure_lmd_report", lambda stock: {"queued": True})

    stocks = [
        SimpleNamespace(symbol="600519", name="茅台", market="CN", id=1),
    ]

    class _StockQuery:
        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return stocks

    class _FakeSessionScanOff(_FakeSession):
        def query(self, model):
            model_key = str(model)
            if model_key.endswith("Stock") or "Stock" in model_key:
                return _StockQuery()
            return super().query(model)

    monkeypatch.setattr(mod, "SessionLocal", lambda: _FakeSessionScanOff(scan_on_startup=False))
    assert mod.bootstrap_all_missing_stocks() == 0


def test_bootstrap_queues_when_startup_scan_enabled(monkeypatch):
    """启动全量扫描开启时应为缺失报告标的排队。"""
    queued: list[str] = []

    def _ensure(stock):
        queued.append(getattr(stock, "symbol", ""))
        return {"queued": True}

    monkeypatch.setattr(mod, "ensure_lmd_report", _ensure)

    stocks = [
        SimpleNamespace(symbol="600519", name="茅台", market="CN", id=1),
    ]

    class _StockQuery:
        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return stocks

    class _FakeSessionScanOn(_FakeSession):
        def query(self, model):
            model_key = str(model)
            if model_key.endswith("Stock") or "Stock" in model_key:
                return _StockQuery()
            return super().query(model)

    monkeypatch.setattr(
        mod,
        "SessionLocal",
        lambda: _FakeSessionScanOn(scan_on_startup=True),
    )
    assert mod.bootstrap_all_missing_stocks() == 1
    assert queued == ["600519"]
