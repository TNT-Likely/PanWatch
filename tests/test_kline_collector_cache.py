"""K线采集的缓存 / 单次取数 / 失败来源日志(批量整治 P0)。

日K一天只定稿一次,但调度任务每轮都逐只重新联网拉 → 批量突发触发第三方限流。
按市场状态缓存 + 摘要单次取数 + 失败日志带调用来源,是止血的核心三件套。
"""

from __future__ import annotations

import logging

from src.collectors import kline_collector
from src.models.market import MarketCode


def _mk_bars(n: int) -> list[kline_collector.KlineData]:
    """造 n 根有波动的日K,够算各项指标。"""
    out = []
    for i in range(n):
        close = 10.0 + (i % 7)
        out.append(
            kline_collector.KlineData(
                date=f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                open=close,
                close=close,
                high=close + 1,
                low=close - 1,
                volume=100.0 + i,
            )
        )
    return out


def test_get_klines_caches_within_ttl(monkeypatch):
    """同一只 K线在 TTL 内应命中内存缓存,不重复联网(避免批量突发触发限流)。"""
    monkeypatch.setattr(kline_collector, "_use_marketdata_kline", lambda: False)
    calls = {"n": 0}
    bars = _mk_bars(130)

    def fake_fetch(symbol, market, days):
        calls["n"] += 1
        return list(bars)

    monkeypatch.setattr(kline_collector, "_fetch_tencent_klines", fake_fetch)
    monkeypatch.setattr(kline_collector, "_fetch_eastmoney_klines", lambda *a, **k: [])

    c = kline_collector.KlineCollector(MarketCode.CN)
    c.get_klines("600519", days=120)
    c.get_klines("600519", days=120)

    assert calls["n"] == 1, f"第二次应命中缓存,实际联网 {calls['n']} 次"


def test_cache_serves_shorter_request_from_longer_entry(monkeypatch):
    """缓存里已有较长序列时,更短的请求应直接切片返回,不再联网。"""
    monkeypatch.setattr(kline_collector, "_use_marketdata_kline", lambda: False)
    calls = {"n": 0}
    bars = _mk_bars(130)

    def fake_fetch(symbol, market, days):
        calls["n"] += 1
        return list(bars)

    monkeypatch.setattr(kline_collector, "_fetch_tencent_klines", fake_fetch)
    monkeypatch.setattr(kline_collector, "_fetch_eastmoney_klines", lambda *a, **k: [])

    c = kline_collector.KlineCollector(MarketCode.CN)
    c.get_klines("600519", days=120)          # 取并缓存 130 根
    out = c.get_klines("600519", days=30)     # 应从缓存切 30 根

    assert calls["n"] == 1, f"更短请求应命中缓存,实际联网 {calls['n']} 次"
    assert len(out) == 30


def test_empty_result_negative_cached_then_retries(monkeypatch):
    """取数为空时进入短冷却:冷却窗口内不再联网(挡住并发/相邻消费者重复打爆源);
    冷却过后仍会重试,不把瞬时故障永久固化为空。"""
    monkeypatch.setattr(kline_collector, "_use_marketdata_kline", lambda: False)
    calls = {"n": 0}

    def fake_fetch(symbol, market, days):
        calls["n"] += 1
        return []

    monkeypatch.setattr(kline_collector, "_fetch_tencent_klines", fake_fetch)
    monkeypatch.setattr(kline_collector, "_fetch_eastmoney_klines", lambda *a, **k: [])

    c = kline_collector.KlineCollector(MarketCode.CN)
    assert c.get_klines("600519", days=120) == []
    assert c.get_klines("600519", days=120) == []
    assert calls["n"] == 1, "冷却窗口内不应重复联网(防突发打爆数据源)"

    # 模拟冷却到期:应重新联网重试,证明瞬时故障未被永久固化为空
    kline_collector._FAIL_UNTIL.clear()
    assert c.get_klines("600519", days=120) == []
    assert calls["n"] == 2, "冷却过后应重新联网重试"


def test_get_kline_summary_fetches_klines_once(monkeypatch):
    """K线摘要应只取一次 K线(原来 30天 + 120天双取),指标复用同一份。"""
    calls = {"n": 0}
    bars = _mk_bars(130)

    def fake_get_klines(self, symbol, days=60):
        calls["n"] += 1
        return list(bars)

    monkeypatch.setattr(
        kline_collector.KlineCollector, "get_klines", fake_get_klines
    )

    summary = kline_collector.KlineCollector(MarketCode.CN).get_kline_summary("600519")

    assert calls["n"] == 1, f"摘要应只取一次 K线,实际 {calls['n']} 次"
    assert summary.get("ma5") is not None, "指标应基于复用的 K线算出"


def test_failure_log_includes_caller_source(monkeypatch, caplog):
    """失败日志应带上调用来源 [src=...],便于定位是哪个调度任务在刷屏。"""
    monkeypatch.setattr(kline_collector, "_use_marketdata_kline", lambda: False)
    monkeypatch.setattr(kline_collector, "_throttle_tencent", lambda: None)
    monkeypatch.setattr(kline_collector.time, "sleep", lambda *_: None)
    monkeypatch.setattr(kline_collector, "_fetch_eastmoney_klines", lambda *a, **k: [])

    class _Resp:
        text = "kline_dayqfq="  # 空 body → 解析为空 → 触发失败日志

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            return _Resp()

    monkeypatch.setattr(kline_collector.httpx, "Client", _FakeClient)

    with caplog.at_level(logging.WARNING):
        with kline_collector.kline_source("unit_test_src"):
            kline_collector.KlineCollector(MarketCode.CN).get_klines("000001", days=60)

    assert any(
        "[src=unit_test_src]" in r.getMessage() for r in caplog.records
    ), f"失败日志应含来源标记,实际: {caplog.text}"
