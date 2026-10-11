"""Small compatibility helpers for exchange-scoped read-model identities."""
from collections import Counter


def market_value(market) -> str:
    return str(getattr(market, "value", market))


def scoped_keys(instruments) -> dict[tuple[str, str], str]:
    """Keep legacy bare keys only when unambiguous; collisions are qualified."""
    pairs = {(market_value(market), symbol) for symbol, market in instruments}
    counts = Counter(symbol for _, symbol in pairs)
    return {(market, symbol): f"{market}:{symbol}" if counts[symbol] > 1 else symbol
            for market, symbol in pairs}


def instrument_get(mapping: dict, symbol: str, market, default=None):
    code = market_value(market)
    value = mapping.get(f"{code}:{symbol}", mapping.get(symbol, default))
    actual = value.get("market") if isinstance(value, dict) else getattr(value, "market", None)
    return default if actual is not None and market_value(actual) != code else value


def unambiguous_stock_map(stocks) -> dict:
    """Legacy model suggestions lack exchange keys: never save an ambiguous one."""
    stocks = list(stocks)
    counts = Counter(stock.symbol for stock in stocks)
    return {stock.symbol: stock for stock in stocks if counts[stock.symbol] == 1}


def analysis_key(symbol: str, market) -> str:
    """Canadian histories/traces must not share legacy US ticker keys."""
    code = market_value(market)
    return f"{code}:{symbol}" if code in {"TSX", "TSXV"} else symbol


def analysis_trade_date(market, now=None):
    """Toronto for Canada; retain existing deployment-date behavior elsewhere."""
    from datetime import datetime, timezone
    from src.platform.marketdata.models import MarketCode, MARKETS
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("analysis clock must be timezone-aware")
    code = market_value(market)
    if code not in {"TSX", "TSXV"}:
        return (now.astimezone() if now is not None else datetime.now()).date().isoformat()
    return (now or datetime.now(timezone.utc)).astimezone(MARKETS[MarketCode(code)].get_tz()).date().isoformat()
