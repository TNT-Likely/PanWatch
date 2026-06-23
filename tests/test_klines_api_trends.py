"""K 线 API 应提供当日分时曲线端点。"""

from src.collectors.kline_collector import IntradayTrendPoint, IntradayTrendsResult
from src.web.api.klines import get_intraday_trends


def test_get_intraday_trends_returns_points(monkeypatch):
    """trends 端点应序列化分时点列表。"""

    def fake_trends(self, symbol):
        return IntradayTrendsResult(
            symbol=symbol,
            market="CN",
            trade_date="2026-06-23",
            pre_close=1241.41,
            updated_at="2026-06-23T13:08:00+08:00",
            points=[
                IntradayTrendPoint(
                    time="2026-06-23 09:30",
                    price=1239.0,
                    avg_price=1239.0,
                    volume=974,
                    turnover=120678600.0,
                ),
                IntradayTrendPoint(
                    time="2026-06-23 09:31",
                    price=1255.11,
                    avg_price=1242.27,
                    volume=1276,
                    turnover=158840257.0,
                ),
            ],
        )

    monkeypatch.setattr(
        "src.web.api.klines.KlineCollector.get_intraday_trends",
        fake_trends,
    )

    out = get_intraday_trends("600519", market="CN")
    assert out["trade_date"] == "2026-06-23"
    assert out["pre_close"] == 1241.41
    assert len(out["points"]) == 2
    assert out["points"][0]["time"].endswith("09:30")
    assert out["points"][1]["price"] == 1255.11
