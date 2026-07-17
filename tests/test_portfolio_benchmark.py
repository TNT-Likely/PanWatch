"""组合 vs 基准对比(M2):超额收益 / 信息比率 / 相对回撤 + 净值曲线。"""

from __future__ import annotations

from src.collectors.kline_collector import KlineData
from src.core import portfolio_benchmark as pb


def _bars(dates_closes):
    return [
        KlineData(date=d, open=c, close=c, high=c, low=c, volume=0) for d, c in dates_closes
    ]


def test_metrics_outperform_flat_benchmark():
    """基准走平、组合上行 → 超额为正、信息比率为正、相对回撤≈0。"""
    dates = ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06"]
    port = [100, 101, 102, 103, 104]
    bench = [100, 100, 100, 100, 100]
    m = pb.compute_benchmark_metrics(dates, port, bench)
    assert m is not None
    assert m["portfolio_return"] == 4.0
    assert m["benchmark_return"] == 0.0
    assert m["excess_return"] == 4.0
    assert m["information_ratio"] > 0
    assert m["relative_drawdown"] == 0.0
    assert len(m["curve"]) == 5 and m["curve"][0]["portfolio"] == 100.0


def test_metrics_identical_series_zero_excess():
    """组合与基准完全相同 → 超额 0、信息比率 0、相对回撤 0。"""
    dates = ["d1", "d2", "d3"]
    s = [100, 105, 103]
    m = pb.compute_benchmark_metrics(dates, list(s), list(s))
    assert m["excess_return"] == 0.0
    assert m["information_ratio"] == 0.0
    assert m["relative_drawdown"] == 0.0


def test_metrics_invalid_returns_none():
    """长度不足/不等长 → None(不抛)。"""
    assert pb.compute_benchmark_metrics(["d1"], [100], [100]) is None
    assert pb.compute_benchmark_metrics(["d1", "d2"], [100, 101], [100]) is None
    assert pb.compute_benchmark_metrics(["d1", "d2"], [0, 101], [100, 101]) is None


def test_parse_tencent_kline_matches_collector_format():
    """本地 _parse_tencent_kline 解析腾讯 kline JSON 文本,字段与旧版一致。"""
    text = (
        'kline_dayqfq={"data":{"sh000300":{"day":['
        '["2026-01-02","3900.1","3910.5","3915.0","3895.2","123456"],'
        '["2026-01-03","3910.5","3920.0","3925.0","3905.0","234567"]'
        "]}}}"
    )
    bars = pb._parse_tencent_kline(text, "sh000300")
    assert len(bars) == 2
    b0 = bars[0]
    assert b0.date == "2026-01-02"
    assert b0.open == 3900.1
    assert b0.close == 3910.5
    assert b0.high == 3915.0
    assert b0.low == 3895.2
    assert b0.volume == 123456.0
    b1 = bars[1]
    assert b1.date == "2026-01-03"
    assert b1.close == 3920.0


def test_build_portfolio_benchmark_with_mocked_fetch(monkeypatch):
    """组合走平、基准上行 → 超额为负;基准元信息回填。"""
    dates = ["2026-01-02", "2026-01-03", "2026-01-04"]
    monkeypatch.setattr(
        pb, "_fetch_benchmark_series", lambda code, days: (dates, [100.0, 110.0, 121.0])
    )

    def fake_fetch(symbol, market):
        return _bars([(d, 10.0) for d in dates])  # 持仓走平

    res = pb.build_portfolio_benchmark(
        [{"symbol": "600519", "market": "CN", "quantity": 100, "fx": 1.0}],
        days=60,
        benchmark_code="000300",
        kline_fetch=fake_fetch,
    )
    assert res is not None
    assert res["benchmark_code"] == "000300"
    assert res["benchmark_label"] == "沪深300"
    assert res["portfolio_return"] == 0.0
    assert res["benchmark_return"] == 21.0
    assert res["excess_return"] == -21.0
    assert res["relative_drawdown"] < 0
