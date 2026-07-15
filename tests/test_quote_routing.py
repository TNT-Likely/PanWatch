import asyncio

import src.core.marketdata_client as mc


def test_paper_trading_uses_md_quote_rows(monkeypatch):
    """模拟盘 _fetch_quotes_map 应走 md_quote_rows(不再直接碰 orchestrator)。"""
    from src.core.paper_trading_engine import PaperTradingEngine

    calls = []
    monkeypatch.setattr(
        "src.core.paper_trading_engine.md_quote_rows",
        lambda symbols, market: (calls.append((tuple(symbols), market)),
                                 [{"symbol": symbols[0], "current_price": 5.0}])[1],
    )
    eng = PaperTradingEngine()
    out = eng._fetch_quotes_map([("600519", "CN")])
    assert out[("CN", "600519")]["current_price"] == 5.0
    assert calls == [(("600519",), "CN")]


def test_price_alert_uses_md_quote_rows(monkeypatch):
    from src.core.price_alert_engine import PriceAlertEngine
    from src.web.models import Stock

    monkeypatch.setattr(
        "src.core.price_alert_engine.md_quote_rows",
        lambda symbols, market: [{"symbol": symbols[0], "current_price": 7.0}],
    )
    eng = PriceAlertEngine()
    s = Stock(symbol="600519", name="x", market="CN")
    out = asyncio.run(eng._fetch_quotes_map([s]))
    assert out[("CN", "600519")]["current_price"] == 7.0
