"""组合归因(Phase D):各持仓对组合收益的贡献。"""
from __future__ import annotations
from src.collectors.kline_collector import KlineData
from src.modules.portfolio import portfolio_benchmark as pb


def _bars(dc):
    return [KlineData(date=d, open=c, close=c, high=c, low=c, volume=0) for d, c in dc]


def test_attribution_weight_times_return(monkeypatch):
    """等权两只:+20% 贡献+10%,-10% 贡献-5%,按贡献降序。"""
    dates = ["2026-01-02", "2026-01-03", "2026-01-04"]
    monkeypatch.setattr(pb, "_fetch_benchmark_series", lambda code, days: (dates, [100.0, 100.0, 100.0]))
    closes = {"A": [10, 11, 12], "B": [10, 9.5, 9]}

    def fetch(sym, mkt):
        return _bars([(d, closes[sym][i]) for i, d in enumerate(dates)])

    res = pb.build_attribution(
        [
            {"symbol": "A", "market": "CN", "quantity": 100, "fx": 1.0, "name": "甲"},
            {"symbol": "B", "market": "CN", "quantity": 100, "fx": 1.0, "name": "乙"},
        ],
        days=60,
        kline_fetch=fetch,
    )
    a = next(r for r in res if r["symbol"] == "A")
    b = next(r for r in res if r["symbol"] == "B")
    assert a["return_pct"] == 20.0 and round(a["contribution_pct"], 1) == 10.0
    assert b["return_pct"] == -10.0 and round(b["contribution_pct"], 1) == -5.0
    assert res[0]["symbol"] == "A"  # 降序
