"""AU(ASX)专属行情 vendor —— Yahoo chart v8 直连。

不复用/不改动 HK/US 的 yfinance vendor:AU 走独立适配器,
已验证的 US/HK 提供方路径保持原样。零 crumb / 零 cookie,
与 YahooKlineVendor 同一条 chart v8 通道。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from marketdata.http import market_get, record_error
from marketdata.symbol import Market, Symbol
from marketdata.types import Quote
from marketdata.vendors.base import QuoteVendor

logger = logging.getLogger(__name__)

_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_EXPECTED_CURRENCY = "AUD"


def _tof(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class YahooAUQuoteVendor(QuoteVendor):
    name = "yahoo_au"
    supports_markets = {"AU"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        if not symbols:
            return []
        proxy = config.get("proxy")
        out: list[Quote] = []
        for s in symbols:
            if s.market != Market.AU:
                continue
            ysym = s.to_yfinance()
            try:
                payload = market_get(
                    _CHART_URL.format(sym=ysym), host_key="query2.finance.yahoo.com",
                    params={"interval": "1d", "range": "1d"},
                    headers={"User-Agent": _UA},
                    timeout=10, retries=2, parse="json", proxy=proxy,
                    log_label="Yahoo AU 行情", symbol=ysym,
                )
            except Exception as e:
                record_error(f"yahoo_au: {type(e).__name__}: {e}")
                continue
            result = ((payload or {}).get("chart") or {}).get("result") or []
            if not result:
                record_error(f"yahoo_au: empty chart payload for {ysym}")
                continue
            meta = result[0].get("meta") or {}
            price = _tof(meta.get("regularMarketPrice"))
            prev = _tof(meta.get("chartPreviousClose") or meta.get("previousClose"))
            currency = str(meta.get("currency") or "")
            if price is None:
                record_error(f"yahoo_au: no price for {ysym}")
                continue
            if currency != _EXPECTED_CURRENCY:
                # 报价币种与挂牌币种不符:拒绝该报价,估值侧按缺失处理。
                record_error(f"yahoo_au: currency {currency!r} != {_EXPECTED_CURRENCY} for {ysym}")
                continue
            source_ts = datetime.fromtimestamp(meta["regularMarketTime"], tz=timezone.utc) \
                if meta.get("regularMarketTime") else None
            quote_date = source_ts.astimezone(self._sydney()).date() if source_ts else None
            out.append(Quote(
                symbol=s.code, market=s.market, current_price=price,
                name=str(meta.get("shortName") or s.code),
                prev_close=prev,
                change_pct=((price - prev) / prev * 100) if prev and prev > 0 else None,
                timestamp=datetime.now(timezone.utc),
                quote_date=quote_date, source_timestamp=source_ts,
                source=self.name, currency=currency,
            ))
        return out

    @staticmethod
    def _sydney():
        from zoneinfo import ZoneInfo
        return ZoneInfo("Australia/Sydney")
