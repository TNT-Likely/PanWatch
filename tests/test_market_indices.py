"""市场指数 API 测试"""
from src.web.api import market as market_api


def test_market_indices_config_includes_new_us_indices():
    """指数列表应包含标普500、纳斯达克100与美元指数。"""
    names = {idx["name"] for idx in market_api.MARKET_INDICES}
    assert "标普500" in names
    assert "纳斯达克100" in names
    assert "美元指数" in names
    assert "沪深300" in names


def test_get_market_indices_merges_tencent_and_eastmoney(monkeypatch):
    """应合并腾讯与东财两路行情并保留配置顺序。"""
    def fake_tencent(symbols):
        assert "usINX" in symbols
        assert "usNDX" in symbols
        return [
            {"symbol": ".INX", "current_price": 5000.0, "change_pct": 0.5, "change_amount": 25.0, "prev_close": 4975.0},
            {"symbol": ".NDX", "current_price": 18000.0, "change_pct": -0.2, "change_amount": -36.0, "prev_close": 18036.0},
        ]

    def fake_eastmoney(secid):
        assert secid == "100.UDI"
        return {
            "current_price": 104.5,
            "change_pct": 0.1,
            "change_amount": 0.1,
            "prev_close": 104.4,
        }

    monkeypatch.setattr(market_api, "_fetch_tencent_quotes", fake_tencent)
    monkeypatch.setattr(market_api, "_fetch_eastmoney_index", fake_eastmoney)

    import asyncio

    rows = asyncio.run(market_api.get_market_indices())
    by_name = {row["name"]: row for row in rows}

    assert by_name["标普500"]["current_price"] == 5000.0
    assert by_name["纳斯达克100"]["change_pct"] == -0.2
    assert by_name["美元指数"]["current_price"] == 104.5
    assert rows[0]["name"] == "上证指数"
