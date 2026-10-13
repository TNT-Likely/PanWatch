import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.enablement import require_market_enabled
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.database import get_db
from src.platform.marketdata.quote_display import daily_quote_fields
from src.web.errors import api_error
from marketdata.symbol import Symbol

router = APIRouter()


class QuoteItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")


class QuoteBatchRequest(BaseModel):
    items: list[QuoteItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise api_error(400, "market_unsupported", f"不支持的市场: {market}")


def _canonical_symbol(raw: str, market: MarketCode) -> str:
    """请求代码在入口归一化:.AX 后缀/大小写统一为规范代码;与显式市场冲突时 400。"""
    try:
        return Symbol.parse(raw, market.value).code
    except ValueError as exc:
        raise api_error(400, "symbol_market_conflict", str(exc))


def _quote_to_response(symbol: str, market: MarketCode, quote: dict | None) -> dict:
    if not quote:
        return {
            "symbol": symbol,
            "market": market.value,
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
            **daily_quote_fields(market.value, quote),
        }

    return {
        "symbol": symbol,
        "market": market.value,
        "name": quote.get("name"),
        "current_price": quote.get("current_price"),
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
        **daily_quote_fields(market.value, quote),
    }


@router.get("/{symbol}")
async def get_quote(symbol: str, market: str = "CN", db: Session = Depends(get_db)):
    """获取单只股票实时行情"""
    market_code = _parse_market(market)
    require_market_enabled(db, market_code.value)
    symbol_code = _canonical_symbol(symbol, market_code)
    rows = await asyncio.to_thread(md_quote_rows, [symbol_code], market_code.value)
    if not rows:
        raise api_error(404, "quote_not_found", "行情不存在")
    quote_map = {item.get("symbol"): item for item in rows}
    quote = quote_map.get(symbol_code)
    if not quote:
        raise api_error(404, "quote_not_found", "行情不存在")
    return _quote_to_response(symbol_code, market_code, quote)


@router.post("/batch")
async def get_quotes_batch(payload: QuoteBatchRequest, db: Session = Depends(get_db)):
    """批量获取股票实时行情"""
    if not payload.items:
        return []

    for mk in {item.market for item in payload.items}:
        require_market_enabled(db, _parse_market(mk).value)

    canonical_items = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        canonical_items.append((market_code, _canonical_symbol(item.symbol, market_code)))
    market_items: dict[MarketCode, list[str]] = {}
    for market_code, code in canonical_items:
        market_items.setdefault(market_code, []).append(code)

    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        rows = await asyncio.to_thread(md_quote_rows, symbols, market_code.value)
        quotes_by_market[market_code] = {item.get("symbol"): item for item in rows}

    results = []
    for market_code, code in canonical_items:
        quote = quotes_by_market.get(market_code, {}).get(code)
        results.append(_quote_to_response(code, market_code, quote))

    return results
