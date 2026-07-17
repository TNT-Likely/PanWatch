"""指数取数(K线 + market.py /indices)路由测试"""
import asyncio

import src.collectors.kline_collector as kc
import src.web.api.market as mkt


def test_get_index_klines_uses_marketdata(monkeypatch):
    """get_index_klines 走 md.index_klines(同一 INDEX_SECID 语义),转换为 KlineData。"""
    from marketdata.types import Bar

    captured: dict = {}

    class _MD:
        def index_klines(self, code, *, market, days):
            captured["code"] = code
            captured["market"] = market
            captured["days"] = days
            return [Bar(date="2026-07-01", open=3180.0, close=3200.0, high=3210.0, low=3170.0, volume=1e8)]

    monkeypatch.setattr(kc, "get_market_data", lambda: _MD())

    out = kc.get_index_klines("000001", kc.MarketCode.CN, days=120)

    assert captured == {"code": "000001", "market": "CN", "days": 120}
    assert len(out) == 1 and isinstance(out[0], kc.KlineData)
    assert out[0].date == "2026-07-01" and out[0].close == 3200.0


def test_get_market_indices_uses_marketdata(monkeypatch):
    """/indices 走 md.index_quotes;quote_map/response_symbol 匹配逻辑与返回字段不变。"""
    captured: dict = {}

    class _MD:
        def index_quotes(self, tencent_symbols):
            captured["symbols"] = list(tencent_symbols)
            return [
                {
                    "symbol": "000001",
                    "name": "上证指数",
                    "current_price": 3200.0,
                    "change_pct": 0.63,
                    "change_amount": 20.0,
                    "prev_close": 3180.0,
                },
            ]

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())

    out = asyncio.run(mkt.get_market_indices())

    assert captured["symbols"] == [idx["tencent_symbol"] for idx in mkt.MARKET_INDICES]
    sh = next(i for i in out if i["symbol"] == "000001")
    assert sh["current_price"] == 3200.0 and sh["change_pct"] == 0.63
    # 未命中行情的指数仍返回基本信息占位(current_price=None),匹配逻辑不变
    hsi = next(i for i in out if i["symbol"] == "HSI")
    assert hsi["current_price"] is None
