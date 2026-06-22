"""K线获取:并发合并 + 失败负缓存。

复活的 Phase 0-4 批量消费者(entry_candidates/strategy_engine/backtest)+ 组合归因
会在收盘后并发地对同一批标的取 K 线。源短暂故障时,若失败结果既不缓存也不合并,
每个并发消费者都会各自打一次 eastmoney(出现 "Server disconnected" 日志风暴),
且空结果不缓存导致每轮重复打爆。这里固化两条防线:
  1) 同一标的的并发取数合并为一次联网;
  2) 取数失败后在冷却窗口内不再联网(负缓存)。
"""

from __future__ import annotations

import threading
import time

import pytest

from src.collectors import kline_collector as kc
from src.models.market import MarketCode


@pytest.fixture(autouse=True)
def _clear_caches():
    """每个用例前后清空进程级缓存,避免相互污染。"""
    for name in ("_KLINE_CACHE", "_EASTMONEY_CACHE", "_EASTMONEY_FAIL_UNTIL", "_FAIL_UNTIL", "_FETCH_LOCKS"):
        d = getattr(kc, name, None)
        if isinstance(d, dict):
            d.clear()
    yield
    for name in ("_KLINE_CACHE", "_EASTMONEY_CACHE", "_EASTMONEY_FAIL_UNTIL", "_FAIL_UNTIL", "_FETCH_LOCKS"):
        d = getattr(kc, name, None)
        if isinstance(d, dict):
            d.clear()


def test_failed_fetch_is_negative_cached(monkeypatch):
    """同一标的取数失败后,冷却窗口内再次调用不再联网(负缓存)。"""
    calls = {"n": 0}

    def fake_tencent(symbol, market, days):
        calls["n"] += 1
        return []

    monkeypatch.setattr(kc, "_fetch_tencent_klines", fake_tencent)
    monkeypatch.setattr(kc, "_fetch_eastmoney_klines", lambda *a, **k: [])

    col = kc.KlineCollector(MarketCode.CN)
    assert col.get_klines("600519") == []
    assert col.get_klines("600519") == []  # 冷却窗口内,应直接短路
    assert calls["n"] == 1, f"失败后应负缓存,实际联网 {calls['n']} 次"


def test_concurrent_same_symbol_fetches_coalesced(monkeypatch):
    """同一标的的并发取数应合并为一次联网(防突发打爆数据源)。"""
    calls = {"n": 0}
    guard = threading.Lock()

    def slow_tencent(symbol, market, days):
        with guard:
            calls["n"] += 1
        time.sleep(0.25)
        return []

    monkeypatch.setattr(kc, "_fetch_tencent_klines", slow_tencent)
    monkeypatch.setattr(kc, "_fetch_eastmoney_klines", lambda *a, **k: [])

    col = kc.KlineCollector(MarketCode.CN)
    threads = [threading.Thread(target=lambda: col.get_klines("600519")) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 1, f"5 并发应合并为 1 次联网,实际 {calls['n']} 次"


def test_different_symbols_not_blocked(monkeypatch):
    """不同标的使用不同锁,不应相互阻塞(各自联网一次)。"""
    calls = {"n": 0}
    guard = threading.Lock()

    def fake_tencent(symbol, market, days):
        with guard:
            calls["n"] += 1
        return []

    monkeypatch.setattr(kc, "_fetch_tencent_klines", fake_tencent)
    monkeypatch.setattr(kc, "_fetch_eastmoney_klines", lambda *a, **k: [])

    col = kc.KlineCollector(MarketCode.CN)
    col.get_klines("600519")
    col.get_klines("000001")
    assert calls["n"] == 2, f"两个不同标的各应联网一次,实际 {calls['n']} 次"


def test_insufficient_result_negative_cached(monkeypatch):
    """取到数据但不足 need(HK 腾讯少量 + eastmoney 补全失败)→ 冷却内不再联网。

    复现 outcome_eval 刷屏:正缓存因 count<need 永不命中,旧逻辑只在"空结果"时负缓存,
    导致每轮都重打 eastmoney 补全源。
    """
    calls = {"n": 0}

    def short_tencent(symbol, market, days):
        calls["n"] += 1
        return [
            kc.KlineData(date=f"2026-01-{i + 1:02d}", open=1, close=1, high=1, low=1, volume=1)
            for i in range(30)
        ]

    monkeypatch.setattr(kc, "_fetch_tencent_klines", short_tencent)
    monkeypatch.setattr(kc, "_fetch_eastmoney_klines", lambda *a, **k: [])

    col = kc.KlineCollector(MarketCode.HK)
    col.get_klines("06082", days=120)  # 拿到 30 < need(120) → 冷却 + 缓存部分
    col.get_klines("06082", days=120)  # 冷却内,服务缓存,不再联网
    assert calls["n"] == 1, f"不足 need 时也应负缓存,实际联网 {calls['n']} 次"


def test_us_kline_skips_eastmoney_and_falls_back_to_yfinance(monkeypatch):
    """腾讯美股仅返回少量K线时,应跳过东财(批量易断连)直接走 yfinance。"""
    few = [
        kc.KlineData(date="2011-06-02", open=1, close=1, high=1, low=1, volume=1),
        kc.KlineData(date="2026-06-18", open=2, close=2, high=2, low=2, volume=2),
    ]
    many = [
        kc.KlineData(
            date=f"2026-01-{(i % 28) + 1:02d}",
            open=10 + i,
            close=10 + i,
            high=11 + i,
            low=9 + i,
            volume=100 + i,
        )
        for i in range(60)
    ]
    em_calls: list[tuple[str, MarketCode, int]] = []
    yf_calls: list[tuple[str, MarketCode, int]] = []

    def fake_em(symbol, market, days):
        em_calls.append((symbol, market, days))
        return list(many)

    def fake_yf(symbol, market, days):
        yf_calls.append((symbol, market, days))
        return list(many)

    monkeypatch.setattr(kc, "_fetch_tencent_klines", lambda *a, **k: list(few))
    monkeypatch.setattr(kc, "_fetch_stooq_us_klines", lambda *a, **k: [])
    monkeypatch.setattr(kc, "_fetch_eastmoney_klines", fake_em)
    monkeypatch.setattr(kc, "_fetch_yfinance_klines", fake_yf)

    col = kc.KlineCollector(MarketCode.US)
    out = col.get_klines("TSM", days=60)

    assert len(out) == 60
    assert em_calls == []
    assert len(yf_calls) == 1
    assert yf_calls[0] == ("TSM", MarketCode.US, 120)


def test_eastmoney_failure_is_negative_cached(monkeypatch):
    """东财取数失败后,冷却窗口内再次调用不再联网。"""
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, *a, **k):
            calls["n"] += 1
            raise RuntimeError("Server disconnected without sending a response")

    monkeypatch.setattr(kc.httpx, "Client", FakeClient)
    monkeypatch.setattr(kc, "_throttle_eastmoney", lambda: None)

    out1 = kc._fetch_eastmoney_klines("06082", MarketCode.HK, 120)
    out2 = kc._fetch_eastmoney_klines("06082", MarketCode.HK, 120)

    assert out1 == []
    assert out2 == []
    assert calls["n"] == 2  # 首次 2 次重试,冷却内不再联网


def test_us_kline_falls_back_to_yfinance_when_eastmoney_empty(monkeypatch):
    """东方财富不可达时,美股应继续回退 yfinance。"""
    few = [
        kc.KlineData(date="2011-06-02", open=1, close=1, high=1, low=1, volume=1),
        kc.KlineData(date="2026-06-18", open=2, close=2, high=2, low=2, volume=2),
    ]
    many = [
        kc.KlineData(
            date=f"2026-01-{(i % 28) + 1:02d}",
            open=10 + i,
            close=10 + i,
            high=11 + i,
            low=9 + i,
            volume=100 + i,
        )
        for i in range(60)
    ]

    monkeypatch.setattr(kc, "_fetch_tencent_klines", lambda *a, **k: list(few))
    monkeypatch.setattr(kc, "_fetch_eastmoney_klines", lambda *a, **k: [])
    monkeypatch.setattr(kc, "_fetch_stooq_us_klines", lambda *a, **k: [])
    monkeypatch.setattr(
        kc, "_fetch_yfinance_klines", lambda symbol, market, days: list(many)
    )

    col = kc.KlineCollector(MarketCode.US)
    out = col.get_klines("AAPL", days=60)

    assert len(out) == 60
