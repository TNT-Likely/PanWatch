"""Keep datasource test symbols compatible with the public list[str] contract."""

from marketdata import Market, Symbol


def normalize_test_symbols(values: list | None) -> list[str]:
    """Read legacy {symbol, market} seeds without losing Canadian exchange identity.

    String configuration is left intact. The old Canadian seeds used objects,
    while the API, settings editor and collectors all consume strings.
    """
    result = []
    for value in values or []:
        if isinstance(value, str):
            result.append(value)
            continue
        if not isinstance(value, dict) or not isinstance(value.get("symbol"), str):
            raise ValueError("Invalid datasource test symbol")
        symbol = Symbol.parse(value["symbol"], market=value.get("market"))
        result.append(symbol.to_yfinance() if symbol.market in {Market.TSX, Market.TSXV} else symbol.code)
    return result
