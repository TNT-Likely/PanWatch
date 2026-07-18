"""YFinance 行情 vendor(可选,HK/US)。无状态 + 惰性 import;缺库抛 VendorError。"""

from __future__ import annotations

import logging

from marketdata.errors import VendorError
from marketdata.http import record_error
from marketdata.symbol import Market, Symbol
from marketdata.types import Quote
from marketdata.vendors.base import QuoteVendor

logger = logging.getLogger(__name__)


def _yf_ticker(sym: Symbol) -> str:
    if sym.market == Market.HK:
        return f"{int(sym.code):04d}.HK" if sym.code.isdigit() else f"{sym.code}.HK"
    return sym.code


class YFinanceQuoteVendor(QuoteVendor):
    name = "yfinance"
    supports_markets = {"HK", "US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        if not symbols:
            return []
        try:
            import yfinance as yf
        except ImportError as e:
            raise VendorError("yfinance 未安装,执行 `pip install yfinance` 后启用") from e

        out: list[Quote] = []
        for s in symbols:
            try:
                info = yf.Ticker(_yf_ticker(s)).fast_info
                last = float(info["last_price"]) if info.get("last_price") else None
                if last is None:
                    record_error(f"yfinance {_yf_ticker(s)}: 返回空(last_price 缺失,可能 Yahoo 不可达/被限流/需要代理)")
                    continue
                prev = float(info["previous_close"]) if info.get("previous_close") else None
                chg = last - prev if prev else 0.0
                pct = (chg / prev * 100) if prev else 0.0
                out.append(Quote(
                    symbol=s.code, market=s.market.value, name="",
                    current_price=last, prev_close=prev,
                    open_price=float(info.get("open") or 0),
                    high_price=float(info.get("day_high") or 0),
                    low_price=float(info.get("day_low") or 0),
                    change_amount=chg, change_pct=pct,
                    volume=float(info.get("last_volume") or 0),
                ))
            except Exception as e:
                logger.debug(f"yfinance 拉取 {s.code} 失败: {e}")
                record_error(f"yfinance {_yf_ticker(s)}: {type(e).__name__}: {e}")
        return out
