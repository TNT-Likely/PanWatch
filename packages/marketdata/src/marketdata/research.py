"""Bounded CN research snapshots, separate from the chart's qfq contract.

Public endpoints are not a data license. Host applications must obtain consent
and retain snapshot-adjustment / fixed-universe limitations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    currency: str = "CNY"
    quality_warnings: dict[str, int] = field(default_factory=dict)
    rejected_dates: list[str] = field(default_factory=list)


def _prices(vendor: str, symbol: str, days: int, adjusted: bool) -> list[Bar]:
    if vendor == "tencent":
        adjustment = "hfq" if adjusted else ""
        payload = market_get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            host_key="web.ifzq.gtimg.cn",
            min_interval_s=0.2,
            params={"param": f"{symbol.lower()},day,,,{days},{adjustment}"},
            timeout=8,
            retries=1,
            total_timeout=12,
            parse="json",
            log_label="Qlib research kline",
            symbol=symbol,
        )
        stock = (payload or {}).get("data", {}).get(symbol.lower(), {})
        # Never silently substitute qfq or unadjusted rows for an hfq request.
        rows = stock.get("hfqday" if adjusted else "day", [])
        result = [
            Bar(
                str(r[0]),
                float(r[1]),
                float(r[2]),
                float(r[3]),
                float(r[4]),
                float(r[5]),
            )
            for r in rows
            if len(r) >= 6
        ]
    elif vendor == "eastmoney":
        payload = market_get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            host_key="push2his.eastmoney.com",
            min_interval_s=0.2,
            params={
                "secid": f"{'1' if symbol.startswith('SH') else '0'}.{symbol[2:]}",
                "klt": "101",
                "fqt": "2" if adjusted else "0",
                "lmt": str(days),
                "end": "20500101",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56",
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/",
            },
            timeout=8,
            retries=1,
            total_timeout=12,
            parse="json",
            log_label="Qlib research kline",
            symbol=symbol,
        )
        rows = ((payload or {}).get("data") or {}).get("klines", [])
        result = []
        for row in rows:
            r = row.split(",")
            result.append(
                Bar(
                    str(r[0]),
                    float(r[1]),
                    float(r[2]),
                    float(r[3]),
                    float(r[4]),
                    float(r[5]),
                )
            )
    else:
        raise ValueError("qlib_research_source_unsupported")
    if len({bar.date for bar in result}) != len(result):
        raise ValueError("qlib_source_duplicate_dates")
    return sorted(result, key=lambda bar: bar.date)[-days:]


def fetch_history(
    vendor: str, symbol: str, days: int = 800, completed_through: str | None = None
) -> ResearchHistory:
    if not re.fullmatch(r"(SH(60|68)|SZ(00|30)|BJ(43|83|87|88|92))[0-9]{4}", symbol):
        raise ValueError("qlib_board_not_supported")
    if not 220 <= days <= 800:
        raise ValueError("qlib_history_limit_invalid")
    raw, adjusted = _prices(vendor, symbol, days, False), _prices(
        vendor, symbol, days, True
    )
    if completed_through:
        from datetime import date

        date.fromisoformat(completed_through)
        # Paired intraday responses race: volume / OHLC are still changing.
        # Filter before date/volume/factor validation, not only at host import.
        raw = [bar for bar in raw if bar.date <= completed_through]
        adjusted = [bar for bar in adjusted if bar.date <= completed_through]
    lookup = {bar.date: bar for bar in adjusted}
    if not raw or not adjusted:
        raise ValueError("qlib_source_history_unavailable")
    if set(lookup) != {bar.date for bar in raw}:
        raise ValueError("qlib_adjustment_dates_mismatch")
    bars = []
    for bar in raw:
        hfq = lookup[bar.date]
        numbers = [
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            hfq.open,
            hfq.high,
            hfq.low,
            hfq.close,
            hfq.volume,
        ]
        if (
            not all(math.isfinite(n) for n in numbers)
            or min(numbers[:4] + numbers[5:9]) <= 0
            or bar.volume < 0
            or hfq.volume != bar.volume
        ):
            raise ValueError("qlib_source_prices_invalid")
        factor = hfq.close / bar.close
        # Tencent uses additive cash-dividend adjustment: one scalar cannot
        # reproduce its entire adjusted OHLC. Preserve raw intraday ratios and
        # use the observed CLOSE ratio as a disclosed research-only proxy.
        # A 5% cross-field guard catches mismatched/corrupt price pairs; it is
        # not a certification of corporate-action or PIT accuracy.
        if any(
            abs(getattr(bar, key) * factor - getattr(hfq, key))
            > max(0.02, getattr(hfq, key) * 0.05)
            for key in ("open", "high", "low")
        ):
            raise ValueError("qlib_adjustment_ratio_inconsistent")
        bars.append(
            {
                "date": bar.date,
                "symbol": symbol,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                # Tencent STAR uses shares; its other equity rows and
                # Eastmoney equity histories use lots of 100 shares.
                "volume": bar.volume
                * (1 if vendor == "tencent" and symbol.startswith("SH68") else 100),
                "factor": factor,
            }
        )
    return ResearchHistory(vendor=vendor, bars=bars, calendar=[])


def fetch_calendar(vendor: str, days: int = 800) -> list[str]:
    """Observed SSE index sessions, not a guessed weekday calendar."""
    return [bar.date for bar in _prices(vendor, "SH000001", days, False)]


def yahoo_chart(symbol: str, days: int = 800, proxy: str | None = None):
    if not 220 <= days <= 800:
        raise ValueError("qlib_history_limit_invalid")
    payload = market_get(
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
        host_key="query2.finance.yahoo.com",
        min_interval_s=0.3,
        params={"interval": "1d", "range": "5y"},
        headers={"User-Agent": "PanWatch/1.0"},
        timeout=8,
        total_timeout=12,
        retries=1,
        parse="json",
        proxy=proxy,
    )
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        raise ValueError("qlib_source_history_unavailable")
    return result[0]


def fetch_overseas_history(
    symbol: str,
    market: str,
    days: int = 800,
    completed_through: str | None = None,
    proxy: str | None = None,
) -> ResearchHistory:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if market == "HK" and re.fullmatch(r"HK_\d{5}", symbol):
        ticker, tz = f"{int(symbol[3:]):04d}.HK", ZoneInfo("Asia/Hong_Kong")
    elif market == "US" and re.fullmatch(r"US_[A-Z][A-Z0-9.\-]{0,19}", symbol):
        ticker, tz = symbol[3:].replace(".", "-"), ZoneInfo("America/New_York")
    else:
        raise ValueError("qlib_symbol_market_invalid")
    result = yahoo_chart(ticker, days, proxy)
    indicators = result.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0]
    adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose")
    timestamps = result.get("timestamp") or []
    if adjusted is None or not timestamps:
        raise ValueError("qlib_adjustment_unavailable")
    bars = []
    warnings = {}
    rejected = []
    eligible = [
        (i, stamp)
        for i, stamp in enumerate(timestamps)
        if not completed_through
        or datetime.fromtimestamp(stamp, tz).date().isoformat() <= completed_through
    ][-days:]

    def reject(reason, day):
        warnings[reason] = warnings.get(reason, 0) + 1
        rejected.append(day)

    for i, stamp in eligible:
        day = datetime.fromtimestamp(stamp, tz).date().isoformat()
        if completed_through and day > completed_through:
            continue
        try:
            values = [
                quote[field][i] for field in ("open", "high", "low", "close", "volume")
            ]
            factor = adjusted[i] / values[3]
            if (
                not all(math.isfinite(v) for v in [*values, factor])
                or min(values[:4]) <= 0
                or values[4] < 0
                or factor <= 0
            ):
                reject("invalid_price_or_volume", day)
                continue
            if (
                values[2] > min(values[0], values[3])
                or values[1] < max(values[0], values[3])
                or values[2] > values[1]
            ):
                # Ambiguous supplier OHLC is absent data, not invented extrema.
                reject("invalid_ohlc", day)
                continue
        except (TypeError, KeyError, IndexError, ZeroDivisionError):
            # Provider gaps are absent bars, never zero / forward filled prices.
            reject("missing_quote_fields", day)
            continue
        bars.append(
            dict(
                zip(("open", "high", "low", "close", "volume"), values),
                date=day,
                symbol=symbol,
                factor=factor,
            )
        )
    if len({b["date"] for b in bars}) != len(bars):
        raise ValueError("qlib_source_duplicate_dates")
    if len(rejected) / max(1, len(eligible)) > 0.05:
        raise ValueError("qlib_history_quality_insufficient")
    currency = (result.get("meta") or {}).get("currency")
    if not currency:
        raise ValueError("qlib_source_currency_unavailable")
    return ResearchHistory(
        "yahoo",
        sorted(bars, key=lambda b: b["date"])[-days:],
        [],
        adjustment_method="provider_adjclose_divided_by_provider_quote_close_snapshot",
        currency=currency,
        quality_warnings=warnings,
        rejected_dates=rejected,
    )


def fetch_overseas_calendar(
    market: str, days: int = 800, proxy: str | None = None
) -> list[str]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ticker, timezone = {
        "HK": ("^HSI", "Asia/Hong_Kong"),
        "US": ("^GSPC", "America/New_York"),
    }[market]
    timestamps = yahoo_chart(ticker, days, proxy).get("timestamp") or []
    dates = [
        datetime.fromtimestamp(stamp, ZoneInfo(timezone)).date().isoformat()
        for stamp in timestamps
    ]
    return sorted(set(dates))[-days:]
