"""价格提醒的量比条件应优先用报价字段,不再无谓地拉 K线(批量整治 P2)。

CN/HK 报价已带量比(腾讯 parts[49]);仅当报价缺量比(如美股)才回退 K线摘要。
"""

from __future__ import annotations

import asyncio

from src.modules.market.price_alert_engine import PriceAlertEngine
from src.platform.marketdata.models import MarketCode


def test_volume_ratio_uses_quote_not_kline(monkeypatch):
    """报价带量比时,量比条件直接用报价,不应再拉 K线。"""
    eng = PriceAlertEngine()
    called = {"kline": 0}

    async def fake_kline(market, symbol):
        called["kline"] += 1
        return {"volume_ratio": 9.9}

    monkeypatch.setattr(eng, "_get_kline_summary_cached", fake_kline)

    quote = {"current_price": 10.0, "volume_ratio": 2.5}
    ok, detail = asyncio.run(
        eng._eval_condition(
            {"type": "volume_ratio", "op": ">", "value": 2.0},
            quote,
            MarketCode.CN,
            "600519",
        )
    )

    assert ok is True
    assert detail["actual"] == 2.5
    assert called["kline"] == 0, "有报价量比时不应再拉 K线"


def test_volume_ratio_falls_back_to_kline_when_quote_missing(monkeypatch):
    """报价无量比(如美股)时,量比条件回退到 K线摘要。"""
    eng = PriceAlertEngine()
    called = {"kline": 0}

    async def fake_kline(market, symbol):
        called["kline"] += 1
        return {"volume_ratio": 3.0}

    monkeypatch.setattr(eng, "_get_kline_summary_cached", fake_kline)

    quote = {"current_price": 200.0}  # 无 volume_ratio 字段
    ok, detail = asyncio.run(
        eng._eval_condition(
            {"type": "volume_ratio", "op": ">", "value": 2.0},
            quote,
            MarketCode.US,
            "AAPL",
        )
    )

    assert ok is True
    assert detail["actual"] == 3.0
    assert called["kline"] == 1, "报价缺量比时应回退 K线一次"
