import src.collectors.kline_collector as kc
from src.models.market import MarketCode


def test_fetch_all_sources_uses_marketdata(monkeypatch):
    """_fetch_all_sources 应走 md.klines 并转成 KlineData。"""
    from marketdata.types import Bar

    class _MD:
        def klines(self, symbol, *, market, days, min_count=1):
            return [Bar(date="2026-07-01", open=1, close=2, high=3, low=0.5, volume=10)]
    monkeypatch.setattr(kc, "get_market_data", lambda: _MD())

    out = kc.KlineCollector(MarketCode.CN)._fetch_all_sources("600519", 120)
    assert len(out) == 1 and isinstance(out[0], kc.KlineData)
    assert out[0].date == "2026-07-01" and out[0].close == 2.0
