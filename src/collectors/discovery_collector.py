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

    async def fetch_hot_boards_ftshare(
        self,
        *,
        mode: str = "gainers",
        limit: int = 12,
    ) -> list[HotBoard]:
        """热门板块 ftshare 源(云服务器东财 clist 被断,ftshare 全板块行情兜底)。

        ft_eastmoney_board_latest_kline: 全部东财概念/行业板块最新一根 K 线
        (change_rate=涨跌幅 %, turnover=成交额 元, turnover_rate=换手率 %)。
        返回 508 个板块,这里取一页 100 个排序取 top,避免全量分页。
        """
        import asyncio as _aio

        from marketdata.vendors.ftshare import _get_client

        def _fetch() -> list[HotBoard]:
            client = _get_client({})
            rows = client.call_tool("ft_eastmoney_board_latest_kline", {"page": 1, "page_size": 100}) or []
            boards: list[HotBoard] = []
            # 过滤指数/非板块类(热门板块只显示行业/概念)
            _INDEX_KEYWORDS = ("成", "沪深", "上证", "深证", "HS", "融资融券", "机构重仓", "基金重仓", "QFII", "举牌", "百元股", "高价股")
            for r in rows:
                if not isinstance(r, dict):
                    continue
                name = str(r.get("board_name") or "").strip()
                if not name:
                    continue
                if any(k in name for k in _INDEX_KEYWORDS):
                    continue
                try:
                    change_pct = float(r.get("change_rate") or 0)
                except (TypeError, ValueError):
                    change_pct = None
                try:
                    turnover = float(r.get("turnover") or 0)
                except (TypeError, ValueError):
                    turnover = None
                boards.append(
                    HotBoard(
                        code=str(r.get("board_code") or "").strip(),
                        name=name,
                        change_pct=change_pct,
                        change_amount=None,
                        turnover=turnover,
                    )
                )
            key = "turnover" if mode == "turnover" else "change_pct"
            boards.sort(key=lambda b: (b.turnover if key == "turnover" else b.change_pct) or 0, reverse=True)
            return boards[: max(1, min(int(limit), 100))]

        return await _aio.to_thread(_fetch)

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
