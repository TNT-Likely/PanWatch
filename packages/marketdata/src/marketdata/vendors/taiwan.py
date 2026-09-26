"""TWSE/TPEx official end-of-day stock snapshots (shares and TWD)."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from marketdata.symbol import Symbol
from marketdata.types import Quote
from marketdata.vendors.base import QuoteVendor

_URLS = {
    "TWSE": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "TPEX": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
}
_CACHE: tuple[float, list[dict]] | None = None
_LOCK = threading.Lock()


def _number(value) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _date(value: str) -> str:
    raw = str(value or "")
    if len(raw) == 7 and raw.isdigit():
        return f"{int(raw[:3]) + 1911:04d}-{raw[3:5]}-{raw[5:7]}"
    return raw


def _normalize(row: dict, exchange: str) -> dict | None:
    code_key = "Code" if exchange == "TWSE" else "SecuritiesCompanyCode"
    name_key = "Name" if exchange == "TWSE" else "CompanyName"
    code = str(row.get(code_key) or "").strip()
    if not (code.isdigit() and 4 <= len(code) <= 6):
        return None
    close = _number(row.get("ClosingPrice" if exchange == "TWSE" else "Close"))
    change = _number(row.get("Change"))
    return {
        "symbol": code,
        "name": str(row.get(name_key) or "").strip(),
        "market": "TW",
        "exchange": exchange,
        "date": _date(row.get("Date")),
        "price": close,
        "change_amount": change,
        "prev_close": close - change if close is not None and change is not None else None,
        "open_price": _number(row.get("OpeningPrice" if exchange == "TWSE" else "Open")),
        "high_price": _number(row.get("HighestPrice" if exchange == "TWSE" else "High")),
        "low_price": _number(row.get("LowestPrice" if exchange == "TWSE" else "Low")),
        "volume": _number(row.get("TradeVolume" if exchange == "TWSE" else "TradingShares")),
        "turnover": _number(row.get("TradeValue" if exchange == "TWSE" else "TransactionAmount")),
    }


def taiwan_snapshot() -> list[dict]:
    """Cached official EOD rows; an exchange failure does not erase the other."""
    global _CACHE
    with _LOCK:
        now = time.monotonic()
        if _CACHE and now - _CACHE[0] < 300:
            return _CACHE[1]
        rows: list[dict] = []
        with httpx.Client(timeout=12) as client:
            for exchange, url in _URLS.items():
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    # Both exchanges serve UTF-8 JSON; some responses omit charset.
                    response.encoding = "utf-8"
                    payload = response.json()
                    if isinstance(payload, list):
                        rows.extend(item for raw in payload
                                    if isinstance(raw, dict)
                                    if (item := _normalize(raw, exchange)))
                except (httpx.HTTPError, ValueError):
                    continue
        if rows:
            _CACHE = (now, rows)
        return rows or (_CACHE[1] if _CACHE else [])


class TaiwanQuoteVendor(QuoteVendor):
    name = "taiwan"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        wanted = {s.code for s in symbols}
        out: list[Quote] = []
        for row in taiwan_snapshot():
            if row["symbol"] not in wanted or row["price"] is None:
                continue
            prev = row["prev_close"]
            change = row["change_amount"]
            out.append(Quote(
                symbol=row["symbol"], market="TW", name=row["name"],
                current_price=row["price"], prev_close=prev,
                change_amount=change,
                change_pct=change / prev * 100 if prev and change is not None else None,
                open_price=row["open_price"], high_price=row["high_price"],
                low_price=row["low_price"], volume=row["volume"],
                turnover=row["turnover"],
                timestamp=datetime.fromisoformat(row["date"]).replace(tzinfo=ZoneInfo("Asia/Taipei"))
                if row["date"] else datetime.now(ZoneInfo("Asia/Taipei")),
            ))
        return out
