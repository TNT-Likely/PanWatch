"""Shared API validation for market and canonical stock identity."""

from marketdata.symbol import Symbol

from src.platform.marketdata.models import MarketCode
from src.web.errors import api_error


def parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise api_error(400, "market_unsupported", f"不支持的市场: {market}")


def parse_identity(symbol: str, market: str) -> tuple[MarketCode, str]:
    market_code = parse_market(market)
    try:
        canonical = Symbol.parse(symbol, market_code.value).code
    except ValueError as exc:
        raise api_error(400, "stock_symbol_invalid", "股票代码格式无效或与市场不匹配") from exc
    return market_code, canonical
