import logging
from dataclasses import dataclass

import httpx

from src.collectors.market_http import TTLCache, source_suffix

logger = logging.getLogger(__name__)

# 异动/热门榜单为盘中分钟级数据:短 TTL 缓存,避免市场扫描重复拉。
_DISCOVERY_CACHE = TTLCache(default_ttl_sec=90.0)


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


def _use_marketdata_discovery() -> bool:
    """读灰度开关(独立函数便于测试 monkeypatch)。"""
    from src.config import Settings

    return bool(Settings().use_marketdata)


def get_market_data():
    """惰性导入,避免模块加载时的循环依赖(便于测试 monkeypatch)。"""
    from src.core.marketdata_client import get_market_data as _g

    return _g()


class EastMoneyDiscoveryCollector:
    """Discovery ranks via EastMoney push2 (CN/HK/US)."""

    STOCKS_API = "https://push2.eastmoney.com/api/qt/clist/get"
    BOARDS_API = "https://push2.eastmoney.com/api/qt/clist/get"

    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        verify_ssl: bool = False,
        proxy: str | None = None,
        retries: int = 1,
        backoff_s: float = 0.4,
    ):
        self.timeout_s = float(timeout_s)
        self.verify_ssl = bool(verify_ssl)
        self.proxy = proxy
        self.retries = int(retries)
        self.backoff_s = float(backoff_s)

    async def fetch_hot_stocks(
        self,
        *,
        market: str = "CN",
        mode: str = "turnover",
        limit: int = 20,
    ) -> list[HotStock]:
        if _use_marketdata_discovery():
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

        # ↓↓↓ flag 关:原 async 逻辑一字不动 ↓↓↓
        market = (market or "CN").upper()

        fid = "f6" if mode == "turnover" else "f3"
        fields = "f12,f14,f2,f3,f6,f5"
        if market == "CN":
            fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"  # A-share
        elif market == "HK":
            fs = "m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2"  # HK
        elif market == "US":
            fs = "m:105,m:106,m:107"  # US
        else:
            return []

        params = {
            "pn": 1,
            "pz": max(1, min(int(limit), 100)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }

        data = await self._get_json(self.STOCKS_API, params=params)
        diff = ((data or {}).get("data") or {}).get("diff") or []
        result: list[HotStock] = []
        for it in diff:
            try:
                result.append(
                    HotStock(
                        symbol=str(it.get("f12") or "").strip(),
                        market=market,
                        name=str(it.get("f14") or "").strip(),
                        price=it.get("f2"),
                        change_pct=it.get("f3"),
                        turnover=it.get("f6"),
                        volume=it.get("f5"),
                    )
                )
            except Exception:
                continue
        return result

    async def fetch_hot_boards(
        self,
        *,
        market: str = "CN",
        mode: str = "gainers",
        limit: int = 12,
    ) -> list[HotBoard]:
        if _use_marketdata_discovery():
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

        # ↓↓↓ flag 关:原 async 逻辑一字不动 ↓↓↓
        if market != "CN":
            return []

        fid = "f3" if mode in ("gainers", "hot") else "f6"
        fields = "f12,f14,f2,f3,f4,f6"
        fs = "m:90+t:2"  # industry boards

        params = {
            "pn": 1,
            "pz": max(1, min(int(limit), 100)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }

        data = await self._get_json(self.BOARDS_API, params=params)
        diff = ((data or {}).get("data") or {}).get("diff") or []
        result: list[HotBoard] = []
        for it in diff:
            try:
                result.append(
                    HotBoard(
                        code=str(it.get("f12") or "").strip(),
                        name=str(it.get("f14") or "").strip(),
                        change_pct=it.get("f3"),
                        change_amount=it.get("f4"),
                        turnover=it.get("f6"),
                    )
                )
            except Exception:
                continue
        return result

    async def fetch_board_stocks(
        self,
        *,
        board_code: str,
        mode: str = "gainers",
        limit: int = 20,
    ) -> list[HotStock]:
        if _use_marketdata_discovery():
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

        # ↓↓↓ flag 关:原 async 逻辑一字不动 ↓↓↓
        code = (board_code or "").strip()
        if not code:
            return []

        fid = "f3" if mode in ("gainers", "hot") else "f6"
        fields = "f12,f14,f2,f3,f6,f5"
        fs = f"b:{code}"

        params = {
            "pn": 1,
            "pz": max(1, min(int(limit), 100)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }

        data = await self._get_json(self.STOCKS_API, params=params)
        diff = ((data or {}).get("data") or {}).get("diff") or []
        result: list[HotStock] = []
        for it in diff:
            try:
                result.append(
                    HotStock(
                        symbol=str(it.get("f12") or "").strip(),
                        market="CN",
                        name=str(it.get("f14") or "").strip(),
                        price=it.get("f2"),
                        change_pct=it.get("f3"),
                        turnover=it.get("f6"),
                        volume=it.get("f5"),
                    )
                )
            except Exception:
                continue
        return result

    async def _get_json(self, url: str, *, params: dict) -> dict:
        cache_key = f"{url}?{sorted((params or {}).items())}"
        cached = _DISCOVERY_CACHE.get(cache_key)
        if cached is not None:
            return cached

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }

        last_exc: Exception | None = None
        attempts = max(self.retries, 0) + 1

        for attempt in range(attempts):
            try:
                timeout = httpx.Timeout(
                    self.timeout_s, connect=min(self.timeout_s, 6.0)
                )
                async with httpx.AsyncClient(
                    timeout=timeout,
                    verify=self.verify_ssl,
                    follow_redirects=True,
                    trust_env=False,  # 不吃 env 代理(Telegram/AI 用),仅用显式配置的 self.proxy
                    headers=headers,
                    proxy=self.proxy,
                ) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    _DISCOVERY_CACHE.set(cache_key, data)
                    return data
            except Exception as e:
                last_exc = e
                if attempt < attempts - 1:
                    try:
                        import asyncio

                        await asyncio.sleep(self.backoff_s * (attempt + 1))
                    except Exception:
                        pass
                    continue

        if last_exc is not None:
            logger.warning(
                f"EastMoney discovery request failed: {type(last_exc).__name__}: {last_exc!r}{source_suffix()}"
            )
        return {}
