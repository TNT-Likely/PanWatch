"""Bounded public Yahoo adapter for exchange-listed Canadian securities.

Quotes are delayed/best-effort. Exact identity, type, currency and provider time
are required; unavailable data never falls through to a US namesake.
"""
from datetime import datetime, timezone
import math
from urllib.parse import quote
from zoneinfo import ZoneInfo

from marketdata.errors import VendorError
from marketdata.http import market_get, record_error
from marketdata.symbol import Symbol
from marketdata.types import Bar, Quote
from marketdata.vendors.base import KlineVendor, QuoteVendor

EXCHANGES = {"TOR": "TSX", "TSX": "TSX", "VAN": "TSXV", "TSXV": "TSXV", "TSXVC": "TSXV"}
TORONTO = ZoneInfo("America/Toronto")


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _chart(symbol, days=30):
    payload = market_get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol.to_yfinance(), safe='')}",
        host_key="yahoo_canada", params={"interval": "1d", "range": "5y" if days > 250 else "1y", "events": "history"},
        headers={"User-Agent": "Mozilla/5.0"}, parse="json", timeout=8, retries=1, total_timeout=15,
    )
    rows = ((payload or {}).get("chart") or {}).get("result") or []
    if not rows:
        raise VendorError("Canadian Yahoo chart unavailable")
    result = rows[0]
    meta = result.get("meta") or {}
    if (str(meta.get("symbol", "")).upper() != symbol.to_yfinance()
            or EXCHANGES.get(str(meta.get("exchangeName", "")).upper()) != symbol.market.value
            or meta.get("instrumentType") not in {"EQUITY", "ETF"}
            or meta.get("currency") not in {"CAD", "USD"}):
        raise VendorError("Canadian chart identity, instrument type or currency unavailable/mismatched")
    stamp = _number(meta.get("regularMarketTime"))
    if stamp is None or stamp <= 0:
        raise VendorError("Canadian provider timestamp unavailable")
    return result


class CanadaQuoteVendor(QuoteVendor):
    name = "yahoo_canada"
    supports_markets = {"TSX", "TSXV"}

    def fetch(self, symbols, config):
        out = []
        for symbol in symbols:
            try:
                row = self._decode(symbol)
                if row is not None:
                    out.append(row)
            except Exception as exc:
                record_error(f"Canadian quote unavailable {symbol.to_yfinance()}: {exc}")
        return out

    def _decode(self, symbol):
        result = _chart(symbol)
        meta = result["meta"]
        price = _number(meta.get("regularMarketPrice"))
        if price is None or price <= 0:
            return None
        # chartPreviousClose is the beginning of the requested range, not
        # necessarily yesterday. Use the last earlier raw daily close.
        stamp = datetime.fromtimestamp(float(meta["regularMarketTime"]), timezone.utc)
        source_day = stamp.astimezone(TORONTO).date()
        series = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        prior = [(_number(c), t) for t, c in zip(result.get("timestamp") or [], series.get("close") or [])
                 if datetime.fromtimestamp(t, timezone.utc).astimezone(TORONTO).date() < source_day and _number(c) is not None]
        previous = prior[-1][0] if prior else _number(meta.get("previousClose"))
        return Quote(symbol=symbol.code, market=symbol.market.value,
            name=meta.get("longName") or meta.get("shortName") or symbol.code,
            current_price=price, prev_close=previous,
            open_price=_number(meta.get("regularMarketOpen")),
            high_price=_number(meta.get("regularMarketDayHigh")), low_price=_number(meta.get("regularMarketDayLow")),
            volume=_number(meta.get("regularMarketVolume")),
            change_amount=price-previous if previous else None,
            change_pct=(price/previous-1)*100 if previous else None,
            timestamp=datetime.now(timezone.utc), source_timestamp=stamp, quote_date=source_day,
            source=self.name, currency=meta["currency"], security_type=meta["instrumentType"], delayed=True)


class CanadaKlineVendor(KlineVendor):
    name = "yahoo_canada"
    supports_markets = {"TSX", "TSXV"}

    def fetch(self, symbols, config):
        if not symbols:
            return []
        limit = int(config.get("limit", config.get("days", 120)))
        result = _chart(symbols[0], limit)
        series = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        out = []
        for i, stamp in enumerate(result.get("timestamp") or []):
            values = {k: _number((series.get(k) or [])[i]) if i < len(series.get(k) or []) else None
                      for k in ("open", "high", "low", "close", "volume")}
            if any(values[k] is None or values[k] <= 0 for k in ("open", "high", "low", "close")):
                continue
            out.append(Bar(date=datetime.fromtimestamp(stamp, timezone.utc).astimezone(TORONTO).date().isoformat(),
                           open=values["open"], high=values["high"], low=values["low"], close=values["close"], volume=values["volume"] or 0))
        return out[-limit:]


def search_canadian(query, market="", limit=20):
    payload = market_get("https://query1.finance.yahoo.com/v1/finance/search", host_key="yahoo_canada",
        params={"q": query, "quotesCount": min(limit * 3, 100), "newsCount": 0},
        headers={"User-Agent": "Mozilla/5.0"}, parse="json", timeout=5, retries=0, total_timeout=6)
    out = []
    for item in (payload or {}).get("quotes", []):
        exchange = EXCHANGES.get(str(item.get("exchange", "")).upper())
        if not exchange or (market and exchange != market) or item.get("quoteType") not in {"EQUITY", "ETF"}:
            continue
        try:
            symbol = Symbol.parse(item.get("symbol", ""), exchange)
        except ValueError:
            continue
        out.append({"symbol": symbol.code, "market": exchange,
                    "name": item.get("longname") or item.get("shortname") or symbol.code,
                    "security_type": item["quoteType"], "source": "yahoo_canada"})
    return out[:limit]
