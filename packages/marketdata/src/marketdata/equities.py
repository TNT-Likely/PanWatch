"""Complete current-listed equity directories, never a hand-picked fallback.

Directory coverage is independent of historical-price/prediction coverage.
No ETF/fund, derivative, OTC or delisted-universe claim is made.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
import hashlib
import io
import json
import math
import re
import xml.etree.ElementTree as ET
import zipfile

from marketdata.http import market_get


@dataclass
class EquityCatalog:
    market: str
    source: str
    members: list[dict]
    source_date: str
    raw_count: int
    excluded: dict[str, int] = field(default_factory=dict)
    content_hash: str = ""

    def __post_init__(self):
        if not self.members or len({m["symbol"] for m in self.members}) != len(
            self.members
        ):
            raise ValueError("qlib_catalog_empty_or_duplicate")
        self.members.sort(key=lambda m: m["symbol"])
        self.content_hash = hashlib.sha256(
            json.dumps(self.members, sort_keys=True).encode()
        ).hexdigest()


def fetch_cn_catalog() -> EquityCatalog:
    """Sina hs_a includes SH/SZ/BJ; validate every page and the count twice."""
    base = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center."
    options = {
        "host_key": "vip.stock.finance.sina.com.cn",
        "min_interval_s": 0.2,
        "timeout": 8,
        "total_timeout": 12,
        "retries": 1,
        "parse": "json",
        "headers": {"Referer": "https://finance.sina.com.cn/"},
    }
    total = int(
        market_get(base + "getHQNodeStockCount", params={"node": "hs_a"}, **options)
        or 0
    )
    if not 1000 <= total <= 30000:
        raise ValueError("qlib_catalog_count_invalid")
    rows = []
    for page in range(1, math.ceil(total / 100) + 1):
        data = market_get(
            base + "getHQNodeData",
            params={
                "node": "hs_a",
                "page": page,
                "num": 100,
                "sort": "symbol",
                "asc": 1,
            },
            **options,
        )
        expected = min(100, total - (page - 1) * 100)
        if not isinstance(data, list) or len(data) != expected:
            raise ValueError("qlib_catalog_page_incomplete")
        rows.extend(data)
    if (
        int(
            market_get(base + "getHQNodeStockCount", params={"node": "hs_a"}, **options)
            or 0
        )
        != total
    ):
        raise ValueError("qlib_catalog_changed_retry")
    members = []
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        if not re.fullmatch(
            r"(SH(60|68)|SZ(00|30)|BJ(43|83|87|88|92))[0-9]{4}", symbol
        ):
            raise ValueError("qlib_catalog_unknown_security")
        name = str(row.get("name", "")).strip()
        if not name:
            raise ValueError("qlib_catalog_name_missing")
        board = (
            "BSE"
            if symbol.startswith("BJ")
            else (
                "STAR"
                if symbol.startswith("SH68")
                else "CHINEXT" if symbol.startswith("SZ30") else "MAIN"
            )
        )
        st = "ST" in name.upper()
        members.append(
            {
                "symbol": symbol,
                "market": "CN",
                "code": symbol[2:],
                "name": name,
                "exchange": symbol[:2],
                "board": board,
                "currency": "CNY",
                "lot": 100,
                "price_limit": (
                    0.3
                    if board == "BSE"
                    else 0.2 if board in ("STAR", "CHINEXT") else 0.05 if st else 0.1
                ),
                "special_treatment": st,
            }
        )
    if (
        not any(m["board"] == "BSE" for m in members)
        or not any(m["board"] == "STAR" for m in members)
        or not any(m["board"] == "CHINEXT" for m in members)
    ):
        raise ValueError("qlib_catalog_board_missing")
    return EquityCatalog(
        "CN",
        "sina:hs_a:complete_count_verified",
        members,
        date.today().isoformat(),
        total,
    )


def _xlsx_rows(blob: bytes):
    """Read actual XML rows; HKEX dimension metadata can understate row count."""
    if not blob or len(blob) > 8_000_000:
        raise ValueError("qlib_catalog_document_invalid")
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 80_000_000:
            raise ValueError("qlib_catalog_document_too_large")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [
                "".join(n.itertext())
                for n in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall(
                    "s:si", ns
                )
            ]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        for row in sheet.findall("s:sheetData/s:row", ns):
            values = {}
            for cell in row.findall("s:c", ns):
                column = re.sub(r"\d", "", cell.get("r", ""))
                value = cell.findtext("s:v", "", ns)
                if cell.get("t") == "s":
                    value = strings[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(cell.find("s:is", ns).itertext())
                values[column] = value
            yield values


def fetch_hk_catalog() -> EquityCatalog:
    blob = market_get(
        "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx",
        host_key="www.hkex.com.hk",
        timeout=12,
        total_timeout=18,
        retries=1,
        parse="content",
    )
    rows = list(_xlsx_rows(blob))
    header = next((r for r in rows if r.get("A") == "Stock Code"), None)
    if not header:
        raise ValueError("qlib_catalog_schema_changed")
    columns = {value: column for column, value in header.items()}
    for required in ("Name of Securities", "Category", "Board Lot", "Trading Currency"):
        if required not in columns:
            raise ValueError("qlib_catalog_schema_changed")
    members, excluded, raw_count = [], {}, 0
    for row in rows:
        code = row.get("A", "")
        if not re.fullmatch(r"\d{1,5}", code):
            continue
        raw_count += 1
        category = row.get(columns["Category"], "")
        if category != "Equity":
            excluded[category or "unknown"] = excluded.get(category or "unknown", 0) + 1
            continue
        code = code.zfill(5)
        lot = int(row.get(columns["Board Lot"], "0").replace(",", ""))
        currency = row.get(columns["Trading Currency"], "")
        currency = {"RMB": "CNY"}.get(currency, currency)
        name = row.get(columns["Name of Securities"], "")
        if lot <= 0 or not currency or not name:
            raise ValueError("qlib_catalog_metadata_invalid")
        members.append(
            {
                "symbol": f"HK_{code}",
                "market": "HK",
                "code": code,
                "name": name,
                "exchange": "HKEX",
                "board": row.get(columns.get("Sub-Category", ""), ""),
                "currency": currency,
                "lot": lot,
                "price_limit": None,
                "special_treatment": False,
            }
        )
    if len(members) < 1000:
        raise ValueError("qlib_catalog_suspiciously_small")
    updated = next(
        (r.get("A", "") for r in rows if r.get("A", "").startswith("Updated as at")), ""
    )
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", updated)
    if not match:
        raise ValueError("qlib_catalog_date_missing")
    source_date = date(int(match[3]), int(match[2]), int(match[1])).isoformat()
    return EquityCatalog(
        "HK",
        "HKEX:ListOfSecurities:actual_XML_rows",
        members,
        source_date,
        raw_count,
        excluded,
    )


def fetch_us_catalog() -> EquityCatalog:
    members, excluded, raw_count, dates = [], {}, 0, []
    for filename, nasdaq in (("nasdaqlisted.txt", True), ("otherlisted.txt", False)):
        text = market_get(
            f"https://www.nasdaqtrader.com/dynamic/SymDir/{filename}",
            host_key="www.nasdaqtrader.com",
            timeout=12,
            total_timeout=18,
            retries=1,
            parse="text",
        )
        lines = (text or "").strip().splitlines()
        required = {
            "Security Name",
            "Test Issue",
            "ETF",
            "Symbol" if nasdaq else "ACT Symbol",
        }
        if not nasdaq:
            required.add("Exchange")
        if not lines or not required.issubset(set(lines[0].split("|"))):
            raise ValueError("qlib_catalog_schema_changed")
        if len(lines) < 1000 or not lines[-1].startswith("File Creation Time:"):
            raise ValueError("qlib_catalog_trailer_missing_or_truncated")
        match = re.search(r"(\d{2})(\d{2})(\d{4})", lines[-1])
        if not match:
            raise ValueError("qlib_catalog_date_missing")
        dates.append(date(int(match[3]), int(match[1]), int(match[2])).isoformat())
        for row in csv.DictReader(lines[:-1], delimiter="|"):
            raw_count += 1
            name = row.get("Security Name", "")
            exchange = (
                "NASDAQ"
                if nasdaq
                else {"N": "NYSE", "A": "NYSE_AMERICAN"}.get(row.get("Exchange"), "")
            )
            # Derivative descriptions may themselves mention "common stock".
            derivative = bool(
                re.search(
                    r"\b(warrants?|rights?|notes?|bonds?|debentures?|pfd|(?:preferred|preference) (?:stock|shares))\b",
                    name,
                    re.I,
                )
            )
            bundled_units = bool(
                re.search(r"\bunits?\b", name, re.I)
            ) and not re.search(r"\bcommon units?\b", name, re.I)
            derivative = derivative or bundled_units
            fund = bool(re.search(r"\b(fund|closed.end)\b", name, re.I))
            reason = (
                "test_issue"
                if row.get("Test Issue") != "N"
                else (
                    "etf"
                    if row.get("ETF") != "N"
                    else (
                        "outside_exchanges"
                        if not exchange
                        else "fund_or_derivative" if derivative or fund else None
                    )
                )
            )
            if reason:
                excluded[reason] = excluded.get(reason, 0) + 1
                continue
            code = row.get("Symbol" if nasdaq else "ACT Symbol", "")
            if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,19}", code) or not name:
                excluded["unrecognized_symbol"] = (
                    excluded.get("unrecognized_symbol", 0) + 1
                )
                continue
            members.append(
                {
                    "symbol": f"US_{code}",
                    "market": "US",
                    "code": code,
                    "name": name,
                    "exchange": exchange,
                    "board": exchange,
                    "currency": "USD",
                    "lot": 1,
                    "price_limit": None,
                    "special_treatment": False,
                }
            )
    if len(members) < 1000:
        raise ValueError("qlib_catalog_suspiciously_small")
    return EquityCatalog(
        "US",
        "NasdaqTrader:nasdaqlisted+otherlisted:trailer_verified",
        members,
        min(dates),
        raw_count,
        excluded,
    )


def fetch_catalog(market: str) -> EquityCatalog:
    return {"CN": fetch_cn_catalog, "HK": fetch_hk_catalog, "US": fetch_us_catalog}[
        market
    ]()
