import logging
import time

import httpx
from fastapi import APIRouter, HTTPException

from src.config import Settings
from src.core.notifier import get_global_proxy
from src.collectors.discovery_collector import EastMoneyDiscoveryCollector


router = APIRouter()

logger = logging.getLogger(__name__)


_cache: dict[str, tuple[float, object]] = {}


def _resolve_proxy() -> str:
    # Prefer UI-configured proxy, fallback to env settings.
    try:
        return (get_global_proxy() or "").strip() or (
            Settings().http_proxy or ""
        ).strip()
    except Exception:
        return ""


def _cache_get(key: str, ttl_s: int) -> object | None:
    now = time.time()
    hit = _cache.get(key)
    if not hit:
        return None
    ts, obj = hit
    if now - ts > ttl_s:
        return None
    return obj


def _cache_set(key: str, obj: object) -> None:
    _cache[key] = (time.time(), obj)


@router.get("/stocks")
async def get_hot_stocks(market: str = "CN", mode: str = "turnover", limit: int = 20):
    """Hot stocks for discovery.

    mode: turnover | gainers
    """

    market = (market or "CN").upper()
    mode = (mode or "turnover").lower()
    if mode not in ("turnover", "gainers"):
        raise HTTPException(400, f"不支持的 mode: {mode}")

    key = f"stocks:{market}:{mode}:{int(limit)}"
    cached = _cache_get(key, ttl_s=45)
    if cached is not None:
        return cached

    proxy = _resolve_proxy() or None
    collector = EastMoneyDiscoveryCollector(timeout_s=15.0, proxy=proxy, retries=1)
    try:
        items = await collector.fetch_hot_stocks(market=market, mode=mode, limit=limit)
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError) as e:
        logger.warning(f"discovery stocks connect timeout: {e!r}")
        raise HTTPException(
            503, "热门股票数据源连接超时（可能需要配置代理 http_proxy）"
        )
    except Exception as e:
        logger.warning(f"discovery stocks failed: {type(e).__name__}: {e!r}")
        raise HTTPException(503, "热门股票数据源不可用")
    data = [
        {
            "symbol": it.symbol,
            "name": it.name,
            "price": it.price,
            "change_pct": it.change_pct,
            "turnover": it.turnover,
            "volume": it.volume,
        }
        for it in items
    ]
    _cache_set(key, data)
    return data


@router.get("/boards")
async def get_hot_boards(market: str = "CN", mode: str = "gainers", limit: int = 12):
    """Hot boards (industry) for discovery.

    mode: gainers | turnover
    """

    market = (market or "CN").upper()
    mode = (mode or "gainers").lower()
    if mode not in ("gainers", "turnover", "hot"):
        raise HTTPException(400, f"不支持的 mode: {mode}")

    key = f"boards:{market}:{mode}:{int(limit)}"
    cached = _cache_get(key, ttl_s=60)
    if cached is not None:
        return cached

    proxy = _resolve_proxy() or None
    collector = EastMoneyDiscoveryCollector(timeout_s=15.0, proxy=proxy, retries=1)
    try:
        items = await collector.fetch_hot_boards(market=market, mode=mode, limit=limit)
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError) as e:
        logger.warning(f"discovery boards connect timeout: {e!r}")
        raise HTTPException(
            503, "热门板块数据源连接超时（可能需要配置代理 http_proxy）"
        )
    except Exception as e:
        logger.warning(f"discovery boards failed: {type(e).__name__}: {e!r}")
        raise HTTPException(503, "热门板块数据源不可用")
    data = [
        {
            "code": it.code,
            "name": it.name,
            "change_pct": it.change_pct,
            "change_amount": it.change_amount,
            "turnover": it.turnover,
        }
        for it in items
    ]
    _cache_set(key, data)
    return data


@router.get("/boards/{board_code}/stocks")
async def get_board_stocks(board_code: str, mode: str = "gainers", limit: int = 20):
    """Top stocks in a board."""

    code = (board_code or "").strip()
    if not code:
        raise HTTPException(400, "缺少板块代码")

    mode = (mode or "gainers").lower()
    if mode not in ("gainers", "turnover", "hot"):
        raise HTTPException(400, f"不支持的 mode: {mode}")

    key = f"board_stocks:{code}:{mode}:{int(limit)}"
    cached = _cache_get(key, ttl_s=60)
    if cached is not None:
        return cached

    proxy = _resolve_proxy() or None
    collector = EastMoneyDiscoveryCollector(timeout_s=15.0, proxy=proxy, retries=1)
    try:
        items = await collector.fetch_board_stocks(
            board_code=code, mode=mode, limit=limit
        )
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError) as e:
        logger.warning(f"discovery board_stocks connect timeout: {e!r}")
        raise HTTPException(
            503, "板块成分股数据源连接超时（可能需要配置代理 http_proxy）"
        )
    except Exception as e:
        logger.warning(f"discovery board_stocks failed: {type(e).__name__}: {e!r}")
        raise HTTPException(503, "板块成分股数据源不可用")
    data = [
        {
            "symbol": it.symbol,
            "name": it.name,
            "price": it.price,
            "change_pct": it.change_pct,
            "turnover": it.turnover,
            "volume": it.volume,
        }
        for it in items
    ]
    _cache_set(key, data)
    return data
