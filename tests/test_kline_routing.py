import src.collectors.kline_collector as kc
from src.models.market import MarketCode


def test_fetch_all_sources_flag_on_uses_marketdata(monkeypatch):
    """flag 开时 _fetch_all_sources 应走 md.klines 并转成 KlineData。"""
    from marketdata.types import Bar
    monkeypatch.setattr(kc, "_use_marketdata_kline", lambda: True)

    class _MD:
        def klines(self, symbol, *, market, days, min_count=1):
            return [Bar(date="2026-07-01", open=1, close=2, high=3, low=0.5, volume=10)]
    monkeypatch.setattr(kc, "get_market_data", lambda: _MD())

    out = kc.KlineCollector(MarketCode.CN)._fetch_all_sources("600519", 120)
    assert len(out) == 1 and isinstance(out[0], kc.KlineData)
    assert out[0].date == "2026-07-01" and out[0].close == 2.0


def test_fetch_all_sources_flag_off_unchanged(monkeypatch):
    """flag 关时不碰 md;仍走原 _fetch_tencent_klines 链路(此处 stub 掉网络验证路径)。"""
    monkeypatch.setattr(kc, "_use_marketdata_kline", lambda: False)
    monkeypatch.setattr(kc, "_fetch_tencent_klines", lambda s, m, d: [
        kc.KlineData(date="2026-07-02", open=1, close=2, high=3, low=0.5, volume=10)])
    # 隔离真实网络(CN 分支在不足时会兜底调 eastmoney;此处不测该兜底路径,故 stub 为空)。
    monkeypatch.setattr(kc, "_fetch_eastmoney_klines", lambda *a, **k: [])
    out = kc.KlineCollector(MarketCode.CN)._fetch_all_sources("600519", 5)
    assert len(out) == 1 and out[0].date == "2026-07-02"
