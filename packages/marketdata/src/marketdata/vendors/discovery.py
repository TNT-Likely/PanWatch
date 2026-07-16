"""发现(东财热门榜)vendor:单源、市场级、非 symbol 模型。

移植自 PanWatch src/collectors/discovery_collector.py 的
fetch_hot_stocks(L55-105)/fetch_hot_boards(L107-149)/fetch_board_stocks(L151-196)/
_get_json(L198-248):fid/fields/fs/params 计算与 f-code 字段映射(f12/f14/f2/f3/f4/f5/f6)
逐一照搬。原实现是 async(httpx.AsyncClient);此处改为同步 market_get。

不继承 marketdata.vendors.base.Vendor —— discovery 是市场级、单源、非 symbol 的取数,
硬套 symbol-based 失败转移 Engine 是设计错配,故不进 Engine/不进 DataSource taxonomy。
vendor 内不做缓存(TTL 缓存留宿主 collector)。
"""
from __future__ import annotations

from typing import Any

from marketdata.http import market_get
from marketdata.types import HotBoard, HotStock

_STOCKS_API = "https://push2.eastmoney.com/api/qt/clist/get"
_BOARDS_API = "https://push2.eastmoney.com/api/qt/clist/get"
_HOST_KEY = "push2.eastmoney.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}


def _normalize_diff(data: dict | None) -> list[dict]:
    """东财 clist 的 diff 字段可能是 list,也可能是 dict(按 index 为 key)。统一成 list。"""
    diff = ((data or {}).get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        return list(diff.values())
    return diff


class DiscoveryVendor:
    """东财热门榜(股票/板块/板块成分)。不继承 Vendor(市场级、非 symbol)。"""

    def hot_stocks(
        self,
        *,
        market: str = "CN",
        mode: str = "turnover",
        limit: int = 20,
        proxy: str | None = None,
    ) -> list[HotStock]:
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

        data = self._get_json(_STOCKS_API, params=params, proxy=proxy)
        result: list[HotStock] = []
        for it in _normalize_diff(data):
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

    def hot_boards(
        self,
        *,
        market: str = "CN",
        mode: str = "gainers",
        limit: int = 12,
        proxy: str | None = None,
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

        data = self._get_json(_BOARDS_API, params=params, proxy=proxy)
        result: list[HotBoard] = []
        for it in _normalize_diff(data):
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

    def board_stocks(
        self,
        *,
        board_code: str,
        mode: str = "gainers",
        limit: int = 20,
        proxy: str | None = None,
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

        data = self._get_json(_STOCKS_API, params=params, proxy=proxy)
        result: list[HotStock] = []
        for it in _normalize_diff(data):
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

    def _get_json(self, url: str, *, params: dict, proxy: str | None) -> dict[str, Any]:
        data = market_get(
            url,
            host_key=_HOST_KEY,
            params=params,
            headers=_HEADERS,
            proxy=proxy,
            verify=False,
            parse="json",
            retries=1,
            timeout=10,
            min_interval_s=0.0,
            log_label="发现榜单",
        )
        return data or {}
