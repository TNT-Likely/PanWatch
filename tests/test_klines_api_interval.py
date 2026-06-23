"""K 线 API 应按 interval 路由到日 K 或分钟 K 采集。"""

from src.collectors.kline_collector import KlineData
from src.web.api.klines import get_klines


def test_get_klines_routes_intraday_to_collector(monkeypatch):
    """interval=m5/m30 时应调用 get_intraday_klines 而非日 K 聚合。"""
    captured: dict = {}

    def fake_intraday(self, symbol, interval="m30", count=240):
        captured["symbol"] = symbol
        captured["interval"] = interval
        captured["count"] = count
        return [
            KlineData(
                date="2026-06-22 09:35",
                open=10.0,
                close=10.2,
                high=10.3,
                low=9.9,
                volume=1000,
            )
        ]

    def fail_daily(self, symbol, days=60):
        raise AssertionError("不应走日 K 路径")

    monkeypatch.setattr(
        "src.web.api.klines.KlineCollector.get_intraday_klines",
        fake_intraday,
    )
    monkeypatch.setattr(
        "src.web.api.klines.KlineCollector.get_klines",
        fail_daily,
    )

    out = get_klines("600519", market="CN", interval="m5", count=120)
    assert captured["interval"] == "m5"
    assert captured["count"] == 120
    assert out["interval"] == "m5"
    assert len(out["klines"]) == 1
    assert out["klines"][0]["date"].endswith("09:35")
