"""YFinance 行情 vendor —— Yahoo chart v8 直连(零 crumb / 零 cookie)。

yfinance 库的 fast_info 依赖 crumb/cookie 握手,Yahoo 对非浏览器 UA 一律 429,
故行情走与 YahooKlineVendor 同一条 chart v8 路径,从 meta 取实时价。
"""

from __future__ import annotations

import logging

from marketdata.http import market_get, record_error
from marketdata.symbol import Symbol
from marketdata.types import Quote
from marketdata.vendors.base import QuoteVendor

logger = logging.getLogger(__name__)

_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _tof(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _last_candle(result: dict, key: str) -> float | None:
    indicators = (result.get("indicators") or {})
    quote = (indicators.get("quote") or [{}])[0] or {}
    values = [v for v in (quote.get(key) or []) if v is not None]
    return _tof(values[-1]) if values else None


class YFinanceQuoteVendor(QuoteVendor):
    name = "yfinance"
    supports_markets = {"HK", "US", "AU"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        if not symbols:
            return []
        proxy = config.get("proxy")
        out: list[Quote] = []
        for s in symbols:
            ysym = s.to_yfinance()
            try:
                payload = market_get(
                    _CHART_URL.format(sym=ysym), host_key="query2.finance.yahoo.com",
                    params={"interval": "1d", "range": "1d"},
                    headers={"User-Agent": _UA},
                    timeout=10, retries=2, parse="json", proxy=proxy,
                    log_label="Yahoo 行情", symbol=ysym,
                )
            except Exception as e:
                record_error(f"yfinance {ysym}: {type(e).__name__}: {e}")
                continue
            if not isinstance(payload, dict):
                record_error(f"yfinance {ysym}: 响应非 JSON")
                continue
            result = ((payload.get("chart") or {}).get("result")) or []
            if not result:
                record_error(f"yfinance {ysym}: chart 无结果(可能 Yahoo 不可达/被限流)")
                continue
            meta = (result[0] or {}).get("meta") or {}
            last = _tof(meta.get("regularMarketPrice"))
            if last is None:
                record_error(f"yfinance {ysym}: regularMarketPrice 缺失(可能 Yahoo 不可达/被限流/需要代理)")
                continue
            prev = _tof(meta.get("previousClose")) or _tof(meta.get("chartPreviousClose"))
            chg = (last - prev) if prev else 0.0
            pct = (chg / prev * 100) if prev else 0.0
            out.append(Quote(
                symbol=s.code, market=s.market.value, name="",
                current_price=last,
                prev_close=prev,
                open_price=_last_candle(result[0] or {}, "open"),
                high_price=_tof(meta.get("regularMarketDayHigh")),
                low_price=_tof(meta.get("regularMarketDayLow")),
                change_amount=chg, change_pct=pct,
                volume=_tof(meta.get("regularMarketVolume")),
            ))
        return out
