"""K 线 API — 通过 KlineOrchestrator 按 datasource 优先级调用各 provider。

调用链:
1. 先尝试 KlineOrchestrator (走 DB 中 enabled 的 datasource,按 priority 排序)
2. 失败后 fallback 到内置 KlineCollector(硬编码腾讯→Stooq→东财)

这样启用了 通达信K线 且优先级更高时,业务 K 线会优先通过 injoyai/tdx 获取。
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime

from pydantic import BaseModel, Field

from src.collectors.kline_collector import KlineCollector
from src.models.market import MarketCode

router = APIRouter()


class KlineItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")
    days: int | None = Field(default=60, description="K线天数")
    interval: str | None = Field(default="1d", description="周期: 1d/1w/1m/5m/15m/30m/60m")


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
        raise HTTPException(400, f"不支持的市场: {market}")


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


def _interval_to_ktype(interval: str) -> str:
    """将 KlineItem 的 interval 转换为 provider 的 ktype。"""
    mapping = {
        "1d": "day", "day": "day",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "60m": "60m",
    }
    return mapping.get(interval, "day")


def _is_minute_interval(interval: str) -> bool:
    return interval in ("5m", "15m", "30m", "60m")


def _try_orchestrator_first(
    symbol: str, market: str, days: int, ktype: str = "day"
) -> list | None:
    """尝试通过 KlineOrchestrator 获取 K 线(按 datasource 优先级调用 provider)。

    返回 list[KlineData] 或 None(全部 provider 失败时)。
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        from src.core.providers.base import ProviderRequest
        from src.core.providers.orchestrator import get_kline_orchestrator

        req = ProviderRequest(
            symbols=(symbol,),
            market=market,
            extra=(("days", str(days)), ("type", ktype)),
        )
        resp = get_kline_orchestrator().fetch_sync(req, cache_ttl_sec=60)
        if resp.success and resp.data:
            logger.info(
                "K线 通过 orchestrator 获取成功: provider=%s symbol=%s market=%s type=%s",
                resp.provider, symbol, market, ktype,
            )
            return resp.data
        else:
            logger.warning(
                "K线 orchestrator 全部失败(%s): %s, 回退内置采集器",
                symbol, resp.error,
            )
    except Exception as e:
        logger.warning(
            "K线 orchestrator 异常(%s): %s, 回退内置采集器", symbol, e,
        )
    return None


@router.get("/{symbol}")
def get_klines(symbol: str, market: str = "CN", days: int = 60, interval: str = "1d"):
    """获取单只股票K线数据"""
    market_code = _parse_market(market)
    ktype = _interval_to_ktype(interval)

    # 先尝试 orchestrator(按 datasource 优先级)
    data = _try_orchestrator_first(symbol, market_code.value, days, ktype)
    if data is None:
        # fallback 到内置 KlineCollector
        collector = KlineCollector(market_code)
        data = collector.get_klines(symbol, days=days)

    # 分钟 K 线跳过周/月聚合
    if not _is_minute_interval(interval):
        data = _aggregate_klines(data, interval)
    return {
        "symbol": symbol,
        "market": market_code.value,
        "days": days,
        "interval": interval,
        "klines": _serialize_klines(data),
    }


@router.post("/batch")
def get_klines_batch(payload: KlineBatchRequest):
    """批量获取K线数据"""
    if not payload.items:
        return []

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        days = item.days or 60
        interval = item.interval or "1d"
        ktype = _interval_to_ktype(interval)

        # 先尝试 orchestrator(按 datasource 优先级)
        data = _try_orchestrator_first(item.symbol, market_code.value, days, ktype)
        if data is None:
            # fallback 到内置 KlineCollector
            collector = KlineCollector(market_code)
            data = collector.get_klines(item.symbol, days=days)

        if not _is_minute_interval(interval):
            data = _aggregate_klines(data, interval)
        results.append(
            {
                "symbol": item.symbol,
                "market": market_code.value,
                "days": days,
                "interval": interval,
                "klines": _serialize_klines(data),
            }
        )

    return results


@router.get("/{symbol}/summary")
def get_kline_summary(symbol: str, market: str = "CN"):
    """获取单只股票K线摘要"""
    market_code = _parse_market(market)
    collector = KlineCollector(market_code)

    # 先尝试 orchestrator 获取 K 线数据,再生成摘要
    klines = _try_orchestrator_first(symbol, market_code.value, days=120)
    if klines is not None:
        summary = collector.get_kline_summary(klines=klines)
    else:
        # fallback 到内置采集器
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

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        collector = KlineCollector(market_code)

        # 先尝试 orchestrator 获取 K 线数据,再生成摘要
        klines = _try_orchestrator_first(item.symbol, market_code.value, days=120)
        if klines is not None:
            summary = collector.get_kline_summary(klines=klines)
        else:
            # fallback 到内置采集器
            summary = collector.get_kline_summary(item.symbol)

        results.append(
            {
                "symbol": item.symbol,
                "market": market_code.value,
                "summary": summary,
            }
        )

    return results