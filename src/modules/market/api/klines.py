from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter
from datetime import datetime

from pydantic import BaseModel, Field

from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.models import MarketCode
from src.web.errors import api_error

router = APIRouter()


class KlineItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")
    days: int | None = Field(default=60, description="K线天数")
    interval: str | None = Field(default="1d", description="周期: 1d/1w/1m")


class KlineBatchRequest(BaseModel):
    items: list[KlineItem]


class KlineSummaryItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")


class KlineSummaryBatchRequest(BaseModel):
    items: list[KlineSummaryItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise api_error(400, "market_unsupported", f"不支持的市场: {market}")


def _serialize_klines(klines) -> list[dict]:
    return [
        {
            "date": k.date,
            "open": k.open,
            "close": k.close,
            "high": k.high,
            "low": k.low,
            "volume": k.volume,
        }
        for k in klines
    ]


def _aggregate_klines(klines, interval: str) -> list:
    """Aggregate daily klines to week/month."""

    iv = (interval or "1d").lower()
    if iv in ("1d", "day", "d"):
        return klines
    if iv not in ("1w", "1m", "week", "month", "w", "m"):
        return klines

    parsed = []
    for k in klines or []:
        try:
            dt = datetime.strptime(k.date, "%Y-%m-%d")
        except Exception:
            continue
        parsed.append((dt, k))

    parsed.sort(key=lambda x: x[0])
    buckets: dict[str, list] = {}
    for dt, k in parsed:
        if iv in ("1w", "week", "w"):
            y, w, _ = dt.isocalendar()
            key = f"{y:04d}-W{w:02d}"
        else:
            key = f"{dt.year:04d}-{dt.month:02d}"
        buckets.setdefault(key, []).append((dt, k))

    out = []
    for _, items in buckets.items():
        items.sort(key=lambda x: x[0])
        first = items[0][1]
        last = items[-1][1]
        high = max(it[1].high for it in items)
        low = min(it[1].low for it in items)
        vol = sum(it[1].volume for it in items)
        out.append(
            type(first)(
                date=items[-1][0].strftime("%Y-%m-%d"),
                open=first.open,
                close=last.close,
                high=high,
                low=low,
                volume=vol,
            )
        )
    out.sort(key=lambda k: k.date)
    return out


@router.get("/{symbol}")
def get_klines(symbol: str, market: str = "CN", days: int = 60, interval: str = "1d"):
    """获取单只股票K线数据"""
    market_code = _parse_market(market)
    collector = KlineCollector(market_code)
    klines = collector.get_klines(symbol, days=days)
    klines = _aggregate_klines(klines, interval)
    return {
        "symbol": symbol,
        "market": market_code.value,
        "days": days,
        "interval": interval,
        "klines": _serialize_klines(klines),
    }


@router.post("/batch")
def get_klines_batch(payload: KlineBatchRequest):
    """批量获取K线数据"""
    if not payload.items:
        return []

    keys = [(item.symbol, _parse_market(item.market), item.days or 60, item.interval or "1d")
            for item in payload.items]
    def load_one(key):
        symbol, market, days, interval = key
        klines = KlineCollector(market).get_klines(symbol, days=days)
        return {
            "symbol": symbol, "market": market.value, "days": days, "interval": interval,
            "klines": _serialize_klines(_aggregate_klines(klines, interval)),
        }
    unique = list(dict.fromkeys(keys))
    with ThreadPoolExecutor(max_workers=min(5, len(unique))) as pool:
        rows = dict(zip(unique, pool.map(load_one, unique)))
    return [rows[key] for key in keys]


@router.get("/{symbol}/summary")
def get_kline_summary(symbol: str, market: str = "CN"):
    """获取单只股票K线摘要"""
    market_code = _parse_market(market)
    collector = KlineCollector(market_code)
    summary = collector.get_kline_summary(symbol)
    return {
        "symbol": symbol,
        "market": market_code.value,
        "summary": summary,
    }


@router.post("/summary/batch")
def get_kline_summary_batch(payload: KlineSummaryBatchRequest):
    """批量获取K线摘要"""
    if not payload.items:
        return []

    market_codes = [_parse_market(item.market) for item in payload.items]

    def load_one(symbol: str, market_code: MarketCode):
        try:
            summary = KlineCollector(market_code).get_kline_summary(symbol)
        except Exception as exc:
            summary = {"error": str(exc)}
        return {
            "symbol": symbol,
            "market": market_code.value,
            "summary": summary,
        }

    # 与前端原先的并发上限保持一致，减少批量接口对数据源的瞬时压力。
    keys = [(item.symbol, market) for item, market in zip(payload.items, market_codes)]
    unique_keys = list(dict.fromkeys(keys))
    with ThreadPoolExecutor(max_workers=min(5, len(unique_keys))) as executor:
        futures = {key: executor.submit(load_one, *key) for key in unique_keys}
        return [futures[key].result() for key in keys]
