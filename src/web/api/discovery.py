import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.config import Settings
from src.core.notifier import get_global_proxy
from src.collectors.discovery_collector import EastMoneyDiscoveryCollector
from src.web.database import get_db
from src.web.models import MarketScanSnapshot, Stock


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


def _to_number(value) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)
        if n != n:  # NaN
            return None
        return n
    except Exception:
        return None


def _pick_num(mapping: dict, keys: list[str]) -> float | None:
    for key in keys:
        if key in mapping:
            n = _to_number(mapping.get(key))
            if n is not None:
                return n
    return None


def _normalize_market(market: str) -> str:
    m = (market or "CN").strip().upper()
    return m if m in ("CN", "HK", "US") else "CN"


def _latest_snapshot_stocks(db: Session, market: str, limit: int = 120) -> list[dict]:
    mkt = _normalize_market(market)
    latest = (
        db.query(MarketScanSnapshot.snapshot_date)
        .filter(MarketScanSnapshot.stock_market == mkt)
        .order_by(MarketScanSnapshot.snapshot_date.desc())
        .first()
    )
    if not latest:
        return []
    rows = (
        db.query(MarketScanSnapshot)
        .filter(
            MarketScanSnapshot.stock_market == mkt,
            MarketScanSnapshot.snapshot_date == latest[0],
        )
        .order_by(MarketScanSnapshot.score_seed.desc(), MarketScanSnapshot.updated_at.desc())
        .limit(max(20, min(int(limit), 300)))
        .all()
    )
    out: list[dict] = []
    for row in rows:
        quote = row.quote if isinstance(row.quote, dict) else {}
        out.append(
            {
                "symbol": row.stock_symbol,
                "market": row.stock_market,
                "name": row.stock_name or row.stock_symbol,
                "price": _pick_num(quote, ["price", "current_price", "last", "close"]),
                "change_pct": _pick_num(quote, ["change_pct", "pct_change", "chg_pct"]),
                "turnover": _pick_num(quote, ["turnover", "amount", "turnover_value"]),
                "volume": _pick_num(quote, ["volume", "vol"]),
            }
        )
    return out


async def _hot_stocks_live_or_snapshot(
    *,
    collector: EastMoneyDiscoveryCollector,
    db: Session,
    market: str,
    mode: str,
    limit: int,
) -> list[dict]:
    mkt = _normalize_market(market)
    try:
        items = await collector.fetch_hot_stocks(market=mkt, mode=mode, limit=limit)
        data = [
            {
                "symbol": it.symbol,
                "market": it.market,
                "name": it.name,
                "price": it.price,
                "change_pct": it.change_pct,
                "turnover": it.turnover,
                "volume": it.volume,
            }
            for it in items
        ]
        if data:
            return data
    except Exception as e:
        logger.warning(f"discovery stocks live failed ({mkt}/{mode}): {type(e).__name__}: {e!r}")
    # Snapshot fallback: ensures UI is still usable when live source timeout/unavailable.
    return _latest_snapshot_stocks(db, mkt, limit=max(limit, 40))


def _watchlist_symbols(db: Session, market: str) -> set[str]:
    mkt = _normalize_market(market)
    rows = db.query(Stock.symbol).filter(Stock.market == mkt).all()
    return {str(x[0]).strip() for x in rows if x and x[0]}


def _avg(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _sum(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return float(sum(vals))


def _build_synthetic_boards(
    *,
    market: str,
    stocks: list[dict],
    watchlist: set[str],
    limit: int,
) -> list[dict]:
    mkt = _normalize_market(market)
    if not stocks:
        return []
    # Keep a stable high-quality universe for synthetic themes.
    universe = stocks[: max(30, min(len(stocks), 120))]
    gainers = sorted(universe, key=lambda x: _to_number(x.get("change_pct")) or -999.0, reverse=True)
    turnover = sorted(universe, key=lambda x: _to_number(x.get("turnover")) or 0.0, reverse=True)
    volatility = sorted(universe, key=lambda x: abs(_to_number(x.get("change_pct")) or 0.0), reverse=True)
    watch_related = [x for x in universe if str(x.get("symbol") or "") in watchlist]

    def build_bucket(code: str, name: str, items: list[dict]) -> dict | None:
        if not items:
            return None
        top = items[: min(12, len(items))]
        return {
            "code": f"{mkt}_{code}",
            "name": name,
            "change_pct": _avg([_to_number(x.get("change_pct")) for x in top]),
            "change_amount": None,
            "turnover": _sum([_to_number(x.get("turnover")) for x in top]),
        }

    market_name = {"CN": "A股", "HK": "港股", "US": "美股"}.get(mkt, mkt)
    buckets = [
        build_bucket("GAINERS", f"{market_name}涨幅领先", gainers),
        build_bucket("TURNOVER", f"{market_name}成交额领先", turnover),
        build_bucket("VOLATILITY", f"{market_name}波动活跃", volatility),
        build_bucket("WATCHLIST", f"{market_name}自选关联", watch_related),
    ]
    result = [x for x in buckets if x]
    return result[: max(1, min(int(limit), 20))]


def _stocks_by_synthetic_board(
    *,
    code: str,
    market: str,
    stocks: list[dict],
    watchlist: set[str],
    limit: int,
) -> list[dict]:
    mkt = _normalize_market(market)
    suffix = code.replace(f"{mkt}_", "", 1)
    universe = stocks[: max(30, min(len(stocks), 160))]
    if suffix == "GAINERS":
        ranked = sorted(universe, key=lambda x: _to_number(x.get("change_pct")) or -999.0, reverse=True)
    elif suffix == "TURNOVER":
        ranked = sorted(universe, key=lambda x: _to_number(x.get("turnover")) or 0.0, reverse=True)
    elif suffix == "VOLATILITY":
        ranked = sorted(universe, key=lambda x: abs(_to_number(x.get("change_pct")) or 0.0), reverse=True)
    elif suffix == "WATCHLIST":
        ranked = [x for x in universe if str(x.get("symbol") or "") in watchlist]
        ranked = sorted(ranked, key=lambda x: _to_number(x.get("turnover")) or 0.0, reverse=True)
    else:
        ranked = []
    return ranked[: max(1, min(int(limit), 100))]


@router.get("/stocks")
async def get_hot_stocks(
    market: str = "CN",
    mode: str = "turnover",
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Hot stocks for discovery.

    mode: turnover | gainers
    """

    market = _normalize_market(market)
    mode = (mode or "turnover").lower()
    if mode not in ("turnover", "gainers"):
        raise HTTPException(400, f"不支持的 mode: {mode}")

    key = f"stocks:{market}:{mode}:{int(limit)}"
    cached = _cache_get(key, ttl_s=45)
    if cached is not None:
        return cached

    proxy = _resolve_proxy() or None
    collector = EastMoneyDiscoveryCollector(timeout_s=15.0, proxy=proxy, retries=1)
    data = await _hot_stocks_live_or_snapshot(
        collector=collector,
        db=db,
        market=market,
        mode=mode,
        limit=max(1, min(int(limit), 100)),
    )
    if not data:
        raise HTTPException(
            503, "热门股票数据源不可用（实时源与本地快照均不可用）"
        )
    _cache_set(key, data)
    return data


@router.get("/boards")
async def get_hot_boards(
    market: str = "CN",
    mode: str = "gainers",
    limit: int = 12,
    db: Session = Depends(get_db),
):
    """Hot boards (industry) for discovery.

    mode: gainers | turnover
    """

    market = _normalize_market(market)
    mode = (mode or "gainers").lower()
    if mode not in ("gainers", "turnover", "hot"):
        raise HTTPException(400, f"不支持的 mode: {mode}")

    key = f"boards:{market}:{mode}:{int(limit)}"
    cached = _cache_get(key, ttl_s=60)
    if cached is not None:
        return cached

    proxy = _resolve_proxy() or None
    collector = EastMoneyDiscoveryCollector(timeout_s=15.0, proxy=proxy, retries=1)
    data: list[dict] = []
    # CN: prefer real industry boards; HK/US: synthetic themed buckets from market hot pool.
    if market == "CN":
        try:
            items = await collector.fetch_hot_boards(market=market, mode=mode, limit=limit)
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
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError) as e:
            logger.warning(f"discovery boards connect timeout: {e!r}")
        except Exception as e:
            logger.warning(f"discovery boards failed: {type(e).__name__}: {e!r}")

    if not data:
        stocks = await _hot_stocks_live_or_snapshot(
            collector=collector,
            db=db,
            market=market,
            mode="turnover" if mode == "turnover" else "gainers",
            limit=max(50, int(limit) * 10),
        )
        watchlist = _watchlist_symbols(db, market)
        data = _build_synthetic_boards(
            market=market,
            stocks=stocks,
            watchlist=watchlist,
            limit=limit,
        )
    if not data:
        raise HTTPException(503, "热门板块/主题数据源不可用")
    _cache_set(key, data)
    return data


@router.get("/boards/{board_code}/stocks")
async def get_board_stocks(
    board_code: str,
    mode: str = "gainers",
    limit: int = 20,
    market: str = "CN",
    db: Session = Depends(get_db),
):
    """Top stocks in a board."""

    code = (board_code or "").strip()
    if not code:
        raise HTTPException(400, "缺少板块代码")

    mkt = _normalize_market(market)
    mode = (mode or "gainers").lower()
    if mode not in ("gainers", "turnover", "hot"):
        raise HTTPException(400, f"不支持的 mode: {mode}")

    key = f"board_stocks:{mkt}:{code}:{mode}:{int(limit)}"
    cached = _cache_get(key, ttl_s=60)
    if cached is not None:
        return cached

    if code.startswith(("CN_", "HK_", "US_")):
        proxy = _resolve_proxy() or None
        collector = EastMoneyDiscoveryCollector(timeout_s=15.0, proxy=proxy, retries=1)
        market_from_code = code.split("_", 1)[0]
        stocks = await _hot_stocks_live_or_snapshot(
            collector=collector,
            db=db,
            market=market_from_code,
            mode="turnover" if mode == "turnover" else "gainers",
            limit=max(80, int(limit) * 8),
        )
        watchlist = _watchlist_symbols(db, market_from_code)
        data = _stocks_by_synthetic_board(
            code=code,
            market=market_from_code,
            stocks=stocks,
            watchlist=watchlist,
            limit=limit,
        )
        _cache_set(key, data)
        return data

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
            "market": "CN",
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
