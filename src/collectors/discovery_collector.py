import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HotStock:
    symbol: str
    market: str
    name: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    volume: float | None


@dataclass(frozen=True)
class HotBoard:
    code: str
    name: str
    change_pct: float | None
    change_amount: float | None
    turnover: float | None


def get_market_data():
    """惰性导入,避免模块加载时的循环依赖(便于测试 monkeypatch)。"""
    from src.core.marketdata_client import get_market_data as _g

    return _g()


class EastMoneyDiscoveryCollector:
    """Discovery ranks (CN/HK/US),经 marketdata 包统一取数。"""

    def __init__(self, *, proxy: str | None = None):
        self.proxy = proxy

    async def fetch_hot_stocks(
        self,
        *,
        market: str = "CN",
        mode: str = "turnover",
        limit: int = 20,
    ) -> list[HotStock]:
        import asyncio as _aio

        pkg_items = await _aio.to_thread(
            get_market_data().hot_stocks,
            market=market,
            mode=mode,
            limit=limit,
            proxy=self.proxy,
        )
        return [
            HotStock(
                symbol=it.symbol,
                market=it.market,
                name=it.name,
                price=it.price,
                change_pct=it.change_pct,
                turnover=it.turnover,
                volume=it.volume,
            )
            for it in pkg_items
        ]

    async def fetch_hot_boards(
        self,
        *,
        market: str = "CN",
        mode: str = "gainers",
        limit: int = 12,
    ) -> list[HotBoard]:
        import asyncio as _aio

        pkg_items = await _aio.to_thread(
            get_market_data().hot_boards,
            market=market,
            mode=mode,
            limit=limit,
            proxy=self.proxy,
        )
        return [
            HotBoard(
                code=it.code,
                name=it.name,
                change_pct=it.change_pct,
                change_amount=it.change_amount,
                turnover=it.turnover,
            )
            for it in pkg_items
        ]

    async def fetch_board_stocks(
        self,
        *,
        board_code: str,
        mode: str = "gainers",
        limit: int = 20,
    ) -> list[HotStock]:
        import asyncio as _aio

        pkg_items = await _aio.to_thread(
            get_market_data().board_stocks,
            board_code=board_code,
            mode=mode,
            limit=limit,
            proxy=self.proxy,
        )
        return [
            HotStock(
                symbol=it.symbol,
                market=it.market,
                name=it.name,
                price=it.price,
                change_pct=it.change_pct,
                turnover=it.turnover,
                volume=it.volume,
            )
            for it in pkg_items
        ]
