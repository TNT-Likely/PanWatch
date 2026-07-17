"""md_stock_data 兼容层测试:走 marketdata 包。"""

import src.core.marketdata_client as mc
from src.models.market import MarketCode, StockData


def test_md_stock_data_uses_marketdata(monkeypatch):
    """md_stock_data 应调用 get_market_data().quotes() 并转换为 StockData。"""
    from marketdata.types import Quote

    class _MD:
        def quotes(self, symbols, *, market=None):
            return [Quote(symbol=s, market=market, current_price=10.0, name="X",
                          change_pct=1.0, change_amount=0.1, volume=100.0, turnover=1000.0,
                          open_price=9.9, high_price=10.1, low_price=9.8, prev_close=9.9) for s in symbols]

    monkeypatch.setattr(mc, "get_market_data", lambda: _MD())
    out = mc.md_stock_data(["600519"], "CN")
    assert len(out) == 1 and isinstance(out[0], StockData)
    assert out[0].symbol == "600519" and out[0].current_price == 10.0 and out[0].market == MarketCode.CN
