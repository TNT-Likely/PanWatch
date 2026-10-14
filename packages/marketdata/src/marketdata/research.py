"""Bounded CN research snapshots, separate from the chart's qfq contract.

Public endpoints are not a data license. Host applications must obtain consent
and retain snapshot-adjustment / fixed-universe limitations.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from marketdata.http import market_get
from marketdata.types import Bar


@dataclass
class ResearchHistory:
    vendor: str
    bars: list[dict]
    calendar: list[str]
    adjustment_method: str = "hfq_close_divided_by_raw_close"
    volume_unit: str = "shares"


def _prices(vendor: str, symbol: str, days: int, adjusted: bool) -> list[Bar]:
    if vendor == "tencent":
        adjustment = "hfq" if adjusted else ""
        payload = market_get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            host_key="web.ifzq.gtimg.cn", min_interval_s=0.2,
            params={"param": f"{symbol.lower()},day,,,{days},{adjustment}"},
            timeout=8, retries=1, total_timeout=12, parse="json",
            log_label="Qlib research kline", symbol=symbol,
        )
        stock = (payload or {}).get("data", {}).get(symbol.lower(), {})
        # Never silently substitute qfq or unadjusted rows for an hfq request.
        rows = stock.get("hfqday" if adjusted else "day", [])
        result = [Bar(str(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows if len(r) >= 6]
    elif vendor == "eastmoney":
        payload = market_get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            host_key="push2his.eastmoney.com", min_interval_s=0.2,
            params={"secid": f"{'1' if symbol.startswith('SH') else '0'}.{symbol[2:]}",
                    "klt": "101", "fqt": "2" if adjusted else "0", "lmt": str(days),
                    "end": "20500101", "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
            timeout=8, retries=1, total_timeout=12, parse="json",
            log_label="Qlib research kline", symbol=symbol,
        )
        rows = ((payload or {}).get("data") or {}).get("klines", [])
        result = []
        for row in rows:
            r = row.split(",")
            result.append(Bar(str(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
    else:
        raise ValueError("qlib_research_source_unsupported")
    if len({bar.date for bar in result}) != len(result):
        raise ValueError("qlib_source_duplicate_dates")
    return sorted(result, key=lambda bar: bar.date)[-days:]


def fetch_history(vendor: str, symbol: str, days: int = 800, completed_through: str | None = None) -> ResearchHistory:
    if not re.fullmatch(r"(SH(600|601|603|605)|SZ(000|001|002))[0-9]{3}", symbol):
        raise ValueError("qlib_board_not_supported")
    if not 220 <= days <= 800:
        raise ValueError("qlib_history_limit_invalid")
    raw, adjusted = _prices(vendor, symbol, days, False), _prices(vendor, symbol, days, True)
    if completed_through:
        from datetime import date
        date.fromisoformat(completed_through)
        # Paired intraday responses race: volume / OHLC are still changing.
        # Filter before date/volume/factor validation, not only at host import.
        raw = [bar for bar in raw if bar.date <= completed_through]
        adjusted = [bar for bar in adjusted if bar.date <= completed_through]
    lookup = {bar.date: bar for bar in adjusted}
    if not raw or set(lookup) != {bar.date for bar in raw}:
        raise ValueError("qlib_adjustment_dates_mismatch")
    bars = []
    for bar in raw:
        hfq = lookup[bar.date]
        numbers = [bar.open, bar.high, bar.low, bar.close, bar.volume, hfq.open, hfq.high, hfq.low, hfq.close, hfq.volume]
        if not all(math.isfinite(n) for n in numbers) or min(numbers[:4] + numbers[5:9]) <= 0 or bar.volume < 0 or hfq.volume != bar.volume:
            raise ValueError("qlib_source_prices_invalid")
        factor = hfq.close / bar.close
        # Tencent uses additive cash-dividend adjustment: one scalar cannot
        # reproduce its entire adjusted OHLC. Preserve raw intraday ratios and
        # use the observed CLOSE ratio as a disclosed research-only proxy.
        # A 5% cross-field guard catches mismatched/corrupt price pairs; it is
        # not a certification of corporate-action or PIT accuracy.
        if any(abs(getattr(bar, key) * factor - getattr(hfq, key)) > max(0.02, getattr(hfq, key) * 0.05) for key in ("open", "high", "low")):
            raise ValueError("qlib_adjustment_ratio_inconsistent")
        bars.append({"date": bar.date, "symbol": symbol, "open": bar.open,
                     "high": bar.high, "low": bar.low, "close": bar.close,
                     # Both supported endpoints return main-board stock volume
                     # in lots of 100 shares (indices / STAR are excluded here).
                     "volume": bar.volume * 100, "factor": factor})
    return ResearchHistory(vendor=vendor, bars=bars, calendar=[])


def fetch_calendar(vendor: str, days: int = 800) -> list[str]:
    """Observed SSE index sessions, not a guessed weekday calendar."""
    return [bar.date for bar in _prices(vendor, "SH000001", days, False)]
