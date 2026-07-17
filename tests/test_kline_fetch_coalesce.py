"""K线获取:并发合并 + 失败负缓存。

复活的 Phase 0-4 批量消费者(entry_candidates/strategy_engine/backtest)+ 组合归因
会在收盘后并发地对同一批标的取 K 线。源短暂故障时,若失败结果既不缓存也不合并,
每个并发消费者都会各自打一次 marketdata 包(出现 "Server disconnected" 日志风暴),
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
    for name in ("_KLINE_CACHE", "_FAIL_UNTIL", "_FETCH_LOCKS"):
        d = getattr(kc, name, None)
        if isinstance(d, dict):
            d.clear()
    yield
    for name in ("_KLINE_CACHE", "_FAIL_UNTIL", "_FETCH_LOCKS"):
        d = getattr(kc, name, None)
        if isinstance(d, dict):
            d.clear()


class _FakeMarketData:
    """假的 marketdata.MarketData,只实现 klines(),记录调用次数。"""

    def __init__(self, fetch):
        self._fetch = fetch
        self.calls = 0
        self._guard = threading.Lock()

    def klines(self, symbol, *, market, days, min_count=1):
        with self._guard:
            self.calls += 1
        return self._fetch(symbol, market, days)


def test_failed_fetch_is_negative_cached(monkeypatch):
    """同一标的取数失败后,冷却窗口内再次调用不再联网(负缓存)。"""
    fake = _FakeMarketData(lambda symbol, market, days: [])
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.CN)
    assert col.get_klines("600519") == []
    assert col.get_klines("600519") == []  # 冷却窗口内,应直接短路
    assert fake.calls == 1, f"失败后应负缓存,实际联网 {fake.calls} 次"


def test_concurrent_same_symbol_fetches_coalesced(monkeypatch):
    """同一标的的并发取数应合并为一次联网(防突发打爆数据源)。"""

    def slow_fetch(symbol, market, days):
        time.sleep(0.25)
        return []

    fake = _FakeMarketData(slow_fetch)
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.CN)
    threads = [threading.Thread(target=lambda: col.get_klines("600519")) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert fake.calls == 1, f"5 并发应合并为 1 次联网,实际 {fake.calls} 次"


def test_different_symbols_not_blocked(monkeypatch):
    """不同标的使用不同锁,不应相互阻塞(各自联网一次)。"""
    fake = _FakeMarketData(lambda symbol, market, days: [])
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.CN)
    col.get_klines("600519")
    col.get_klines("000001")
    assert fake.calls == 2, f"两个不同标的各应联网一次,实际 {fake.calls} 次"


def test_insufficient_result_negative_cached(monkeypatch):
    """取到数据但不足 need(HK 源少量返回)→ 冷却内不再联网。

    复现 outcome_eval 刷屏:正缓存因 count<need 永不命中,旧逻辑只在"空结果"时负缓存,
    导致每轮都重打补全源。
    """

    def short_fetch(symbol, market, days):
        return [
            kc.KlineData(date=f"2026-01-{i + 1:02d}", open=1, close=1, high=1, low=1, volume=1)
            for i in range(30)
        ]

    fake = _FakeMarketData(short_fetch)
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.HK)
    col.get_klines("06082", days=120)  # 拿到 30 < need(120) → 冷却 + 缓存部分
    col.get_klines("06082", days=120)  # 冷却内,服务缓存,不再联网
    assert fake.calls == 1, f"不足 need 时也应负缓存,实际联网 {fake.calls} 次"
