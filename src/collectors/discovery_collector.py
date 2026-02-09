import logging
from dataclasses import dataclass

import httpx


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HotStock:
    symbol: str
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


class EastMoneyDiscoveryCollector:
    """Discovery ranks (A-share) via EastMoney push2."""

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
        if market != "CN":
            return []

        fid = "f6" if mode == "turnover" else "f3"
        fields = "f12,f14,f2,f3,f6,f5"
        fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"  # A-share

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
                    trust_env=True,
                    headers=headers,
                    proxy=self.proxy,
                ) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    return resp.json()
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
                f"EastMoney discovery request failed: {type(last_exc).__name__}: {last_exc!r}"
            )
        return {}
