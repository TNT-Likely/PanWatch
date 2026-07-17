"""daily_report 大盘指数取数测试。

覆盖:
- CN 市场走 marketdata 新包 index_quotes,产出正确的 IndexData 列表。
- 非 CN 市场返回空 list（与旧 _get_cn_index 口径一致），且不调用 md。
"""

from __future__ import annotations

import asyncio

from src.agents import daily_report
from src.models.market import IndexData, MarketCode


def _fake_index_items() -> list[dict]:
    return [
        {
            "symbol": "000001",
            "name": "上证指数",
            "current_price": 3123.45,
            "change_pct": 1.23,
            "change_amount": 12.3,
            "prev_close": 3111.15,
            "volume": 100000.0,
            "turnover": 999999999.0,
        },
        {
            "symbol": "399001",
            "name": "深证成指",
            "current_price": 10234.5,
            "change_pct": -0.5,
            "change_amount": -51.2,
            "prev_close": 10285.7,
            "volume": 200000.0,
            "turnover": 888888888.0,
        },
    ]


class _FakeMarketData:
    def __init__(self, items: list[dict]):
        self.items = items
        self.calls: list[list[str]] = []

    def index_quotes(self, tencent_symbols: list[str]) -> list[dict]:
        self.calls.append(list(tencent_symbols))
        return self.items


def test_uses_marketdata_index_quotes(monkeypatch):
    """CN 指数走 md.index_quotes,产出正确的 IndexData 列表。"""
    fake_md = _FakeMarketData(_fake_index_items())
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    indices = asyncio.run(agent._fetch_index_for_market(MarketCode.CN))

    assert fake_md.calls == [["sh000001", "sz399001", "sz399006"]]
    assert len(indices) == 2
    assert all(isinstance(i, IndexData) for i in indices)
    assert indices[0].symbol == "000001"
    assert indices[0].name == "上证指数"
    assert indices[0].market == MarketCode.CN
    assert indices[0].current_price == 3123.45
    assert indices[0].change_pct == 1.23
    assert indices[0].change_amount == 12.3
    assert indices[0].volume == 100000.0
    assert indices[0].turnover == 999999999.0


def test_non_cn_market_returns_empty(monkeypatch):
    """非 CN 市场应返回空 list（与旧 _get_cn_index 口径一致），且不调用 md。"""
    fake_md = _FakeMarketData(_fake_index_items())
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    indices_hk = asyncio.run(agent._fetch_index_for_market(MarketCode.HK))
    indices_us = asyncio.run(agent._fetch_index_for_market(MarketCode.US))

    assert indices_hk == []
    assert indices_us == []
    assert fake_md.calls == []
