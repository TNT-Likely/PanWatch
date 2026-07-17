"""K线采集的缓存 / 单次取数(批量整治 P0)。

日K一天只定稿一次,但调度任务每轮都逐只重新联网拉 → 批量突发触发第三方限流。
按市场状态缓存 + 摘要单次取数,是止血的核心两件套。
"""

from __future__ import annotations

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


class _FakeMarketData:
    """假的 marketdata.MarketData,只实现 klines(),记录调用次数。"""

    def __init__(self, bars):
        self.bars = bars
        self.calls = 0

    def klines(self, symbol, *, market, days, min_count=1):
        self.calls += 1
        return list(self.bars)


def test_get_klines_caches_within_ttl(monkeypatch):
    """同一只 K线在 TTL 内应命中内存缓存,不重复联网(避免批量突发触发限流)。"""
    fake = _FakeMarketData(_mk_bars(130))
    monkeypatch.setattr(kline_collector, "get_market_data", lambda: fake)

    c = kline_collector.KlineCollector(MarketCode.CN)
    c.get_klines("600519", days=120)
    c.get_klines("600519", days=120)

    assert fake.calls == 1, f"第二次应命中缓存,实际联网 {fake.calls} 次"


def test_cache_serves_shorter_request_from_longer_entry(monkeypatch):
    """缓存里已有较长序列时,更短的请求应直接切片返回,不再联网。"""
    fake = _FakeMarketData(_mk_bars(130))
    monkeypatch.setattr(kline_collector, "get_market_data", lambda: fake)

    c = kline_collector.KlineCollector(MarketCode.CN)
    c.get_klines("600519", days=120)          # 取并缓存 130 根
    out = c.get_klines("600519", days=30)     # 应从缓存切 30 根

    assert fake.calls == 1, f"更短请求应命中缓存,实际联网 {fake.calls} 次"
    assert len(out) == 30


def test_empty_result_negative_cached_then_retries(monkeypatch):
    """取数为空时进入短冷却:冷却窗口内不再联网(挡住并发/相邻消费者重复打爆源);
    冷却过后仍会重试,不把瞬时故障永久固化为空。"""
    fake = _FakeMarketData([])
    monkeypatch.setattr(kline_collector, "get_market_data", lambda: fake)

    c = kline_collector.KlineCollector(MarketCode.CN)
    assert c.get_klines("600519", days=120) == []
    assert c.get_klines("600519", days=120) == []
    assert fake.calls == 1, "冷却窗口内不应重复联网(防突发打爆数据源)"

    # 模拟冷却到期:应重新联网重试,证明瞬时故障未被永久固化为空
    kline_collector._FAIL_UNTIL.clear()
    assert c.get_klines("600519", days=120) == []
    assert fake.calls == 2, "冷却过后应重新联网重试"


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
