from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List

from src.models.market import MarketCode
from src.collectors.akshare_collector import _tencent_symbol, _fetch_tencent_quotes
from src.collectors.kline_collector import KlineCollector
from src.core.suggestion_pool import get_latest_suggestions
import time


router = APIRouter()


class InsightItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")


class InsightsBatchRequest(BaseModel):
    items: List[InsightItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"不支持的市场: {market}")


@router.post("/batch")
def insights_batch(payload: InsightsBatchRequest):
    """聚合返回行情 + K线摘要 + 最新建议"""
    if not payload.items:
        return []

    # 1) 批量行情（按市场）
    market_items: dict[MarketCode, list[str]] = {}
    for it in payload.items:
        market_code = _parse_market(it.market)
        market_items.setdefault(market_code, []).append(it.symbol)

    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        tencent_symbols = [_tencent_symbol(s, market_code) for s in symbols]
        try:
            items = _fetch_tencent_quotes(tencent_symbols)
        except Exception:
            items = []
        quotes_by_market[market_code] = {item["symbol"]: item for item in items}

    # 2) K线摘要（逐只，带 60s 简易缓存）
    kline_by_symbol: dict[str, dict] = {}
    now = time.time()
    TTL = 60.0
    # module-level cache
    global _KLINE_CACHE
    try:
        _KLINE_CACHE
    except NameError:
        _KLINE_CACHE = {}
    for it in payload.items:
        market_code = _parse_market(it.market)
        cache_key = f"{market_code.value}:{it.symbol}"
        cached = _KLINE_CACHE.get(cache_key)
        summary = None
        if cached and (now - cached[0] < TTL):
            summary = cached[1]
        else:
            try:
                collector = KlineCollector(market_code)
                summary = collector.get_kline_summary(it.symbol)
            except Exception:
                summary = {}
            _KLINE_CACHE[cache_key] = (now, summary)
        kline_by_symbol[cache_key] = summary

    # 3) 最新建议（建议池）
    stock_keys = [(it.symbol, _parse_market(it.market).value) for it in payload.items]
    latest_sugs = get_latest_suggestions(stock_keys=stock_keys, include_expired=False)

    # 4) 合并返回
    results = []
    for it in payload.items:
        market_code = _parse_market(it.market)
        quote = quotes_by_market.get(market_code, {}).get(it.symbol)
        results.append({
            "symbol": it.symbol,
            "market": market_code.value,
            "quote": {
                "name": quote.get("name") if quote else None,
                "current_price": quote.get("current_price") if quote else None,
                "change_pct": quote.get("change_pct") if quote else None,
                "open_price": quote.get("open_price") if quote else None,
                "high_price": quote.get("high_price") if quote else None,
                "low_price": quote.get("low_price") if quote else None,
                "volume": quote.get("volume") if quote else None,
                "turnover": quote.get("turnover") if quote else None,
            },
            "kline_summary": kline_by_symbol.get(f"{market_code.value}:{it.symbol}", {}),
            "suggestion": latest_sugs.get(f"{market_code.value}:{it.symbol}"),
        })

    return results
