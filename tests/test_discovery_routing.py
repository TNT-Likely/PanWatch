"""发现(东财热门榜)取数路由测试:统一走 marketdata 包"""
import asyncio

import src.collectors.discovery_collector as dc


def test_fetch_hot_stocks_uses_marketdata(monkeypatch):
    """fetch_hot_stocks 走 marketdata 包的 hot_stocks,转换为本模块 HotStock,且透传 proxy。"""
    from marketdata.types import HotStock as MdHotStock

    captured: dict = {}

    class _MD:
        def hot_stocks(self, *, market="CN", mode="turnover", limit=20, proxy=None):
            captured["market"] = market
            captured["mode"] = mode
            captured["limit"] = limit
            captured["proxy"] = proxy
            return [
                MdHotStock(
                    symbol="600519",
                    market="CN",
                    name="贵州茅台",
                    price=1700.0,
                    change_pct=1.23,
                    turnover=999999.0,
                    volume=1000.0,
                )
            ]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())

    collector = dc.EastMoneyDiscoveryCollector(proxy="http://market-scan-proxy:1080")
    out = asyncio.run(collector.fetch_hot_stocks(market="CN", mode="turnover", limit=20))

    assert captured["proxy"] == "http://market-scan-proxy:1080"
    assert captured["market"] == "CN"
    assert captured["mode"] == "turnover"
    assert captured["limit"] == 20

    assert len(out) == 1
    item = out[0]
    assert isinstance(item, dc.HotStock)
    assert item.symbol == "600519"
    assert item.market == "CN"
    assert item.name == "贵州茅台"
    assert item.price == 1700.0
    assert item.change_pct == 1.23
    assert item.turnover == 999999.0
    assert item.volume == 1000.0


def test_fetch_hot_boards_uses_marketdata(monkeypatch):
    """fetch_hot_boards 走 marketdata 包的 hot_boards,转换为本模块 HotBoard,且透传 proxy。"""
    from marketdata.types import HotBoard as MdHotBoard

    captured: dict = {}

    class _MD:
        def hot_boards(self, *, market="CN", mode="gainers", limit=12, proxy=None):
            captured["market"] = market
            captured["mode"] = mode
            captured["limit"] = limit
            captured["proxy"] = proxy
            return [
                MdHotBoard(
                    code="BK0500",
                    name="白酒",
                    change_pct=2.5,
                    change_amount=1.1,
                    turnover=88888.0,
                )
            ]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())

    collector = dc.EastMoneyDiscoveryCollector(proxy="http://market-scan-proxy:1080")
    out = asyncio.run(collector.fetch_hot_boards(market="CN", mode="gainers", limit=12))

    assert captured["proxy"] == "http://market-scan-proxy:1080"
    assert captured["market"] == "CN"
    assert captured["mode"] == "gainers"
    assert captured["limit"] == 12

    assert len(out) == 1
    item = out[0]
    assert isinstance(item, dc.HotBoard)
    assert item.code == "BK0500"
    assert item.name == "白酒"
    assert item.change_pct == 2.5
    assert item.change_amount == 1.1
    assert item.turnover == 88888.0


def test_fetch_board_stocks_uses_marketdata(monkeypatch):
    """fetch_board_stocks 走 marketdata 包的 board_stocks,转换为本模块 HotStock,且透传 proxy。"""
    from marketdata.types import HotStock as MdHotStock

    captured: dict = {}

    class _MD:
        def board_stocks(self, *, board_code, mode="gainers", limit=20, proxy=None):
            captured["board_code"] = board_code
            captured["mode"] = mode
            captured["limit"] = limit
            captured["proxy"] = proxy
            return [
                MdHotStock(
                    symbol="000858",
                    market="CN",
                    name="五粮液",
                    price=150.0,
                    change_pct=3.3,
                    turnover=55555.0,
                    volume=200.0,
                )
            ]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())

    collector = dc.EastMoneyDiscoveryCollector(proxy="http://market-scan-proxy:1080")
    out = asyncio.run(collector.fetch_board_stocks(board_code="BK0500", mode="gainers", limit=20))

    assert captured["proxy"] == "http://market-scan-proxy:1080"
    assert captured["board_code"] == "BK0500"
    assert captured["mode"] == "gainers"
    assert captured["limit"] == 20

    assert len(out) == 1
    item = out[0]
    assert isinstance(item, dc.HotStock)
    assert item.symbol == "000858"
    assert item.market == "CN"
    assert item.name == "五粮液"
    assert item.price == 150.0
    assert item.change_pct == 3.3
    assert item.turnover == 55555.0
    assert item.volume == 200.0
