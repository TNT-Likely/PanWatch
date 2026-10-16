"""stock 行情直连方收口路由测试:验证 dict 形状与 StockData 形状站点均已改走
marketdata 包的兼容层(md_quote_rows / md_stock_data),而非旧的
_fetch_tencent_quotes / AkshareCollector.get_stock_data 直连。
"""

import asyncio

from src.platform.marketdata.models import MarketCode, StockData


def test_insights_fundamental_context_uses_md_quote_rows(monkeypatch):
    """insights._fetch_fundamental_context(dict 消费方)应调用 md_quote_rows 而非旧直连。"""
    import src.modules.research.api.insights as insights

    calls = []

    def _fake_md_quote_rows(symbols, market):
        calls.append((list(symbols), market))
        return [{
            "symbol": symbols[0], "pe_ratio": 10.0, "turnover_rate": 1.0,
            "circulating_market_value": None, "total_market_value": None,
            "high_price": None, "low_price": None, "prev_close": None,
        }]

    monkeypatch.setattr(insights, "md_quote_rows", _fake_md_quote_rows)
    result = asyncio.run(insights._fetch_fundamental_context("600519", "CN"))

    assert calls == [(["600519"], "CN")]
    assert "市盈率" in result
