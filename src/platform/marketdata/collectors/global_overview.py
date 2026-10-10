"""Homepage-only cross-asset observations. Never used for holdings valuation.

Public feeds are indicative, delayed or daily, not executable live prices.
All missing/invalid observations remain null; failures are isolated per feed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import logging
import math
import time
from urllib.parse import quote
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marketdata.http import market_get

logger = logging.getLogger(__name__)

# Provider symbols are deliberately separate from the app's tradable security IDs.
_SPECS = [
    ("csi300", "china", "000300.SS", "Asia/Shanghai", "CNY"),
    ("star50", "china", "000688.SS", "Asia/Shanghai", "CNY"),
    ("chinext", "china", "399006.SZ", "Asia/Shanghai", "CNY"),
    ("hsi", "hongKong", "^HSI", "Asia/Hong_Kong", "HKD"),
    ("hstech", "hongKong", "HSTECH.HK", "Asia/Hong_Kong", "HKD"),
    ("sp500", "us", "^GSPC", "America/New_York", "USD"),
    ("nasdaq", "us", "^IXIC", "America/New_York", "USD"),
    ("dow", "us", "^DJI", "America/New_York", "USD"),
    ("tsx", "canada", "^GSPTSE", "America/Toronto", "CAD"),
    ("cac40", "europe", "^FCHI", "Europe/Paris", "EUR"),
    ("dax", "europe", "^GDAXI", "Europe/Berlin", "EUR"),
    ("ftse100", "europe", "^FTSE", "Europe/London", "GBP"),
    ("stoxx50", "europe", "^STOXX50E", "Europe/Paris", "EUR"),
    ("nikkei225", "asia", "^N225", "Asia/Tokyo", "JPY"),
    ("vnindex", "asia", "^VNINDEX.VN", "Asia/Ho_Chi_Minh", "VND"),
    ("gold", "commodities", "GC=F", "America/New_York", "USD"),
    ("oil", "commodities", "CL=F", "America/New_York", "USD"),
    ("corn", "commodities", "ZC=F", "America/Chicago", "USD"),
    ("btc", "crypto", "BTC-USD", "UTC", "USD"),
    ("eth", "crypto", "ETH-USD", "UTC", "USD"),
]

def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _base(id, group, symbol, zone, currency, kind="index", **extra):
    return dict(id=id, name_key=id, symbol=symbol, name=id, market=group,
                group=group, provider_symbol=symbol, timezone=zone, currency=currency,
                instrument_type=kind, unit="points", provider="Yahoo Finance",
                current_price=None, prev_close=None, change_pct=None,
                change_amount=None, change_bps=None, as_of=None, history_as_of=None,
                status="unavailable", spark=[], **extra)


def _get(url, **kwargs):
    return market_get(url, host_key=url.split("/")[2], timeout=6, retries=0,
                      total_timeout=7, headers={"User-Agent": "PanWatch/1.0"}, **kwargs)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _stale(as_of, *, daily=False, crypto=False):
    try:
        dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age > (7 * 86400 if daily else 36 * 3600 if crypto else 4 * 86400) or age < -86400
    except (ValueError, TypeError):
        return True


def _yahoo_base(spec):
    id, group, symbol, zone, currency = spec
    kind = "futures" if group == "commodities" else "crypto" if group == "crypto" else "index"
    row = _base(id, group, symbol, zone, currency, kind)
    row["unit"] = {"gold": "USD/oz", "oil": "USD/barrel", "corn": "US cents/bushel"}.get(id, "USD" if kind == "crypto" else "points")
    return row


def _yahoo_previous_close(meta, bars, timestamp):
    # Futures daily labels can cover the previous evening. Neither the quote's
    # calendar date nor Yahoo's currentTradingPeriod proves its session: the
    # captured corn periods are midnight-to-23:59 calendar windows. Until a
    # reliable quote-to-session reference is available, keep derived changes
    # unavailable instead of selecting an older (possibly opposite-sign) close.
    # Explicit provider changes are handled separately and do not need bars.
    if meta.get("instrumentType") == "FUTURE":
        return None
    # Date calendar-session candles in the provider's zone, which need not be
    # the instrument's display/exchange zone.
    # A quote's fixed gmtoffset/abbreviation cannot date bars across DST.
    zone = meta.get("exchangeTimezoneName")
    if not isinstance(zone, str) or not zone:
        return None
    try:
        provider_zone = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    quote_day = datetime.fromtimestamp(timestamp, provider_zone).date()
    for ts, value in reversed(bars):
        if datetime.fromtimestamp(ts, provider_zone).date() < quote_day:
            # Preserve an invalid immediate prior bar instead of skipping it.
            return value
    return None


def _yahoo(spec):
    row = _yahoo_base(spec)
    id, group, symbol, zone, currency = spec
    kind = row["instrument_type"]
    try:
        data = _get("https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe=""),
                    params={"range": "1mo", "interval": "1d"}, parse="json")
        result = data["chart"]["result"][0]
        meta = result["meta"]
        # Wrong provider identity is worse than no quote.
        if meta.get("symbol", "").upper() != symbol.upper():
            return row
        expected_type = {"index": "INDEX", "futures": "FUTURE", "crypto": "CRYPTOCURRENCY"}[kind]
        if meta.get("instrumentType") != expected_type:
            return row
        row["provider_name"] = meta.get("longName") or meta.get("shortName") or symbol
        price = _number(meta.get("regularMarketPrice"))
        timestamp = _number(meta.get("regularMarketTime"))
        if price is None or (kind != "futures" and price <= 0) or timestamp is None:
            return row
        # History is optional; malformed/absent bars must not erase a valid quote.
        try:
            close = result["indicators"]["quote"][0].get("close") or []
            timestamps = result.get("timestamp") or []
            bars = sorted({float(ts): _number(value) for ts, value in zip(timestamps, close)
                           if _number(ts) is not None and 0 < float(ts) <= timestamp}.items())
            bars = [(ts, value if value is not None and (kind == "futures" or value > 0) else None)
                    for ts, value in bars]
        except (KeyError, TypeError, IndexError, ValueError):
            bars = []
        history = [(ts, value) for ts, value in bars if value is not None]
        # Prefer explicit same-session changes, even when history has a gap.
        change = _number(meta.get("regularMarketChange"))
        if change is None and _number(meta.get("fulldayPrice")) == price:
            change = _number(meta.get("fulldayChange"))
        # chartPreviousClose is the *range* baseline, not yesterday's close.
        # Only calendar-session assets can fall back to the quote's provider
        # day, even when stale or closed. Unverified futures sessions or missing
        # timezone metadata leave only the derived change unavailable.
        prev = _number(price - change) if change is not None else _yahoo_previous_close(meta, bars, timestamp)
        if prev is not None and kind != "futures" and prev <= 0:
            prev = None
        as_of = _iso(timestamp)
        row.update(current_price=price, prev_close=prev, as_of=as_of,
                   change_amount=price-prev if prev is not None else None,
                   change_pct=(price/prev-1)*100 if prev else None,
                   spark=[v for _, v in history][-20:],
                   history_as_of=_iso(history[-1][0]) if history else None,
                   status="stale" if _stale(as_of, crypto=kind == "crypto") else "delayed")
    except (KeyError, TypeError, IndexError, ValueError, OverflowError) as exc:
        logger.debug("Overview feed unavailable %s: %s", id, exc)
    return row


def _yield_rows(country, provider, symbols):
    rows = []
    zones = {"US": "America/New_York", "CA": "America/Toronto", "CN": "Asia/Shanghai"}
    for tenor, symbol in zip(("3m", "5y", "10y"), symbols):
        row = _base(country.lower()+tenor, "fixedIncome", symbol, zones[country], None,
                    "yield", country=country, tenor=tenor)
        row.update(provider=provider, unit="percent")
        rows.append(row)
    return rows


def _apply_yields(rows, observations):
    """observations: chronological (date ISO, {provider symbol: yield percent})."""
    by_date = {}
    for date, values in observations:
        by_date.setdefault(date, {}).update(values)
    observations = sorted(by_date.items())
    for row in rows:
        series = [(date, _number(values.get(row["provider_symbol"]))) for date, values in observations]
        series = [(date, value) for date, value in series if value is not None]
        if not series:
            continue
        date, value = series[-1]
        prev = series[-2][1] if len(series) > 1 else None
        row.update(current_price=value, prev_close=prev, as_of=date,
                   change_amount=value-prev if prev is not None else None,
                   change_bps=round((value-prev)*100, 6) if prev is not None else None,
                   spark=[value for _, value in series][-20:], history_as_of=date,
                   status="stale" if _stale(date, daily=True) else "daily")
    return rows


def _us_yields():
    rows = _yield_rows("US", "US Treasury", ("BC_3MONTH", "BC_5YEAR", "BC_10YEAR"))
    now = datetime.now(timezone.utc)
    # Two months preserve the previous observation across month boundaries.
    months = {(now.year, now.month)}
    months.add((now.year-1, 12) if now.month == 1 else (now.year, now.month-1))
    observations = []
    for year, month in sorted(months):
        data = _get("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml", params={"data": "daily_treasury_yield_curve", "field_tdr_date_value_month": f"{year}{month:02d}"})
        if not data:
            continue
        root = ET.fromstring(data)
        for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
            fields = {el.tag.split("}")[-1]: el.text for el in entry.iter()}
            date = fields.get("NEW_DATE")
            if date:
                observations.append((date[:10], fields))
    return _apply_yields(rows, observations)

class _TableParser(HTMLParser):
    """Read only table cells; never execute provider HTML or embedded scripts."""
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current = []
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current = []
        if tag in ("td", "th"):
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.current.append("".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and self.current:
            self.rows.append(self.current)


def _china_yields():
    rows = _yield_rows("CN", "ChinaBond / CCDC", ("3M", "5Y", "10Y"))
    now = datetime.now(timezone.utc).date()
    html = _get("https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery",
                params={"startDate": (now-timedelta(days=60)).isoformat(),
                        "endDate": now.isoformat(), "gjqx": "0", "qxId": "hzsylqx", "locale": "en_US"})
    parser = _TableParser()
    parser.feed(html or "")
    observations = []
    for cells in parser.rows:
        if len(cells) < 10 or "government" not in cells[0].lower():
            continue
        try:
            date = datetime.strptime(cells[1], "%Y-%m-%d").date().isoformat()
        except ValueError:
            continue
        observations.append((date, {"3M": cells[2], "5Y": cells[6], "10Y": cells[8]}))
    return _apply_yields(rows, observations)


def _canada_yields():
    symbols = ("TB.CDN.90D.MID", "BD.CDN.5YR.DQ.YLD", "BD.CDN.10YR.DQ.YLD")
    rows = _yield_rows("CA", "Bank of Canada", symbols)
    data = _get("https://www.bankofcanada.ca/valet/observations/" + ",".join(symbols) + "/json",
                params={"recent": 30}, parse="json")
    observations = [(row["d"], {symbol: row.get(symbol, {}).get("v") for symbol in symbols})
                    for row in (data or {}).get("observations", []) if row.get("d")]
    return _apply_yields(rows, observations)


_CACHE = None
_INFLIGHT = None
_REFRESH_TIMEOUT_S = 40


def clear_overview_cache():
    global _CACHE
    _CACHE = None


async def get_global_overview():
    global _INFLIGHT
    if _CACHE and time.monotonic() - _CACHE[0] < 300:
        return _CACHE[1]
    # One cold refresh per process/event loop. A disconnected caller must not
    # cancel the work shared by other dashboard requests.
    if _INFLIGHT is None or _INFLIGHT.done() or _INFLIGHT.get_loop() is not asyncio.get_running_loop():
        _INFLIGHT = asyncio.create_task(_load_global_overview())
    return await asyncio.shield(_INFLIGHT)


async def _load_global_overview():
    global _CACHE
    if _CACHE and time.monotonic() - _CACHE[0] < 300:
        return _CACHE[1]
    # A bounded worker pool prevents 29 simultaneous outgoing requests.
    semaphore = asyncio.Semaphore(4)
    async def run(fn, fallback):
        async with semaphore:
            try:
                return await asyncio.to_thread(fn)
            except Exception as exc:
                logger.warning("Overview provider failed: %s", type(exc).__name__)
                return fallback
    jobs = [(lambda spec=spec: _yahoo(spec), _yahoo_base(spec)) for spec in _SPECS]
    jobs += [(_us_yields, _yield_rows("US", "US Treasury", ("BC_3MONTH", "BC_5YEAR", "BC_10YEAR"))),
             (_canada_yields, _yield_rows("CA", "Bank of Canada", ("TB.CDN.90D.MID", "BD.CDN.5YR.DQ.YLD", "BD.CDN.10YR.DQ.YLD"))),
             (_china_yields, _yield_rows("CN", "ChinaBond / CCDC", ("3M", "5Y", "10Y")))]
    tasks = [asyncio.create_task(run(fn, fallback)) for fn, fallback in jobs]
    done, pending = await asyncio.wait(tasks, timeout=_REFRESH_TIMEOUT_S)
    # Keep completed feeds; never hold the whole overview hostage to one source.
    # Cancelling a queued job prevents more requests from starting. At most four
    # already-running synchronous calls can finish under their HTTP timeout.
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    parts = [task.result() if task in done else fallback
             for task, (_, fallback) in zip(tasks, jobs)]
    rows = [row for part in parts for row in (part if isinstance(part, list) else [part])]
    _CACHE = (time.monotonic(), rows)
    return rows
