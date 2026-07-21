import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.collectors.fund_quotes import _fetch_fund_quotes
from src.core.marketdata_client import md_quote_rows
from src.models.market import MarketCode

router = APIRouter()


class QuoteItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US/FUND")


class QuoteBatchRequest(BaseModel):
    items: list[QuoteItem]


def _parse_market(market: str) -> str:
    value = str(market or "").strip().upper()
    if value in ("CN", "HK", "US", "FUND"):
        return value
    raise HTTPException(400, f"不支持的市场: {market}")


def _quote_to_response(symbol: str, market: str, quote: dict | None) -> dict:
    if not quote:
        return {
            "symbol": symbol,
            "market": market,
            "exchange": None,
            "name": None,
            "current_price": None,
            "change_pct": None,
            "change_amount": None,
            "prev_close": None,
            "open_price": None,
            "high_price": None,
            "low_price": None,
            "volume": None,
            "turnover": None,
            "turnover_rate": None,
            "pe_ratio": None,
            "total_market_value": None,
            "circulating_market_value": None,
            # 基金专用字段
            "gztime": None,
            "jzrq": None,
            "has_estimate": None,
        }

    # 基金专用逻辑
    raw_current_price = quote.get("current_price")
    current_price = raw_current_price
    has_estimate = None
    if market == "FUND":
        has_estimate = raw_current_price is not None
        # 无估值时 fallback 到单位净值
        if raw_current_price is None:
            current_price = quote.get("prev_close")

    return {
        "symbol": symbol,
        "market": market,
        "exchange": quote.get("exchange"),
        "name": quote.get("name"),
        "current_price": current_price,
        "change_pct": quote.get("change_pct"),
        "change_amount": quote.get("change_amount"),
        "prev_close": quote.get("prev_close"),
        "open_price": quote.get("open_price"),
        "high_price": quote.get("high_price"),
        "low_price": quote.get("low_price"),
        "volume": quote.get("volume"),
        "turnover": quote.get("turnover"),
        "turnover_rate": quote.get("turnover_rate"),
        "pe_ratio": quote.get("pe_ratio"),
        "total_market_value": quote.get("total_market_value"),
        "circulating_market_value": quote.get("circulating_market_value"),
        # 基金专用字段
        "gztime": quote.get("gztime"),
        "jzrq": quote.get("jzrq"),
        "has_estimate": has_estimate,
    }


@router.get("/{symbol}")
async def get_quote(symbol: str, market: str = "CN"):
    """获取单只股票/基金实时行情"""
    market_code = _parse_market(market)
    if market_code == "FUND":
        items = _fetch_fund_quotes([symbol])
        quote_map = {item["symbol"]: item for item in items}
        quote = quote_map.get(symbol)
    else:
        enum_market = MarketCode(market_code)
        rows = await asyncio.to_thread(md_quote_rows, [symbol], enum_market.value)
        quote_map = {item.get("symbol"): item for item in rows}
        quote = quote_map.get(symbol)
    if not quote:
        raise HTTPException(404, "行情不存在")
    return _quote_to_response(symbol, market_code, quote)


@router.post("/batch")
async def get_quotes_batch(payload: QuoteBatchRequest):
    """批量获取股票/基金实时行情"""
    if not payload.items:
        return []

    market_items: dict[str, list[str]] = {}
    for item in payload.items:
        market_code = _parse_market(item.market)
        market_items.setdefault(market_code, []).append(item.symbol)

    quotes_by_market: dict[str, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        if market_code == "FUND":
            try:
                items = _fetch_fund_quotes(symbols)
            except Exception:
                items = []
            quotes_by_market[market_code] = {item["symbol"]: item for item in items}
        else:
            enum_market = MarketCode(market_code)
            rows = await asyncio.to_thread(md_quote_rows, symbols, enum_market.value)
            quotes_by_market[market_code] = {item.get("symbol"): item for item in rows}

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        quote = quotes_by_market.get(market_code, {}).get(item.symbol)
        results.append(_quote_to_response(item.symbol, market_code, quote))

    return results
