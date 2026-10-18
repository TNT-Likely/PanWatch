import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.modules.market.api.identity import parse_identity as _parse_identity
from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.models import MarketCode
from src.platform.marketdata.quote_display import daily_quote_fields
from src.web.errors import api_error

router = APIRouter()


class QuoteItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US/TSX/TSXV")


class QuoteBatchRequest(BaseModel):
    items: list[QuoteItem]


def _quote_to_response(symbol: str, market: MarketCode, quote: dict | None) -> dict:
    if not quote:
        return {
            "symbol": symbol,
            "market": market.value,
            "name": None,
            **{key: None for key in ("currency", "security_type", "source", "source_timestamp", "is_delayed")},
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
        **{key: quote.get(key) for key in ("currency", "security_type", "source", "source_timestamp")},
        "is_delayed": quote.get("delayed"),
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
async def get_quote(symbol: str, market: str = "CN"):
    """获取单只股票实时行情"""
    market_code, canonical = _parse_identity(symbol, market)
    rows = await asyncio.to_thread(md_quote_rows, [canonical], market_code.value)
    if not rows:
        raise api_error(404, "quote_not_found", "行情不存在")
    quote_map = {item.get("symbol"): item for item in rows}
    quote = quote_map.get(canonical)
    if not quote:
        raise api_error(404, "quote_not_found", "行情不存在")
    return _quote_to_response(symbol, market_code, quote)


@router.post("/batch")
async def get_quotes_batch(payload: QuoteBatchRequest):
    """批量获取股票实时行情"""
    if not payload.items:
        return []

    # Validate the whole batch before fetching. Keep original item order and
    # response symbols while looking up provider results by canonical identity.
    identities = [_parse_identity(item.symbol, item.market) for item in payload.items]
    market_items: dict[MarketCode, list[str]] = {}
    for market_code, canonical in identities:
        market_items.setdefault(market_code, []).append(canonical)

    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        rows = await asyncio.to_thread(md_quote_rows, list(dict.fromkeys(symbols)), market_code.value)
        quotes_by_market[market_code] = {item.get("symbol"): item for item in rows}

    results = []
    for item, (market_code, canonical) in zip(payload.items, identities):
        quote = quotes_by_market.get(market_code, {}).get(canonical)
        results.append(_quote_to_response(item.symbol, market_code, quote))

    return results
