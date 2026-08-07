"""Twelve Data 行情 vendor(美股为主, 免费 800 req/day, 多 key 池可放大额度)。

从 config["api_key"] 读凭证; Engine 的 KeyPool 负责多 key 轮换与限流冷却。
Twelve Data quote 端点返回美股/ETF/指数/外汇实时报价。
文档: https://twelvedata.com/docs/api-reference/quote
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from marketdata.errors import VendorError
from marketdata.http import record_error
from marketdata.symbol import Market, Symbol
from marketdata.types import Quote
from marketdata.vendors.base import QuoteVendor

logger = logging.getLogger(__name__)

_BASE = "https://api.twelvedata.com/quote"


class TwelveDataQuoteVendor(QuoteVendor):
    name = "twelvedata"
    supports_markets = {"US", "HK", "GLOBAL"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        api_key = config.get("api_key") or ""
        if not api_key:
            raise VendorError("twelvedata 需要 api_key(config['api_key'])")
        if not symbols:
            return []

        out: list[Quote] = []
        for s in symbols:
            code = self._to_td_symbol(s)
            url = f"{_BASE}?symbol={urllib.parse.quote(code)}&apikey={urllib.parse.quote(api_key)}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "PanWatch/1.0"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = resp.read().decode("utf-8", "replace")
                quote = self._parse(data, s)
                if quote is not None:
                    out.append(quote)
            except VendorError:
                # 凭证/限流错误: 透传给 Engine, 触发 KeyPool 冷却+切换(不吞)
                raise
            except urllib.error.HTTPError as e:
                # 401/429 等 HTTP 错误: 读 body 判断, 凭证类透传给 Engine 切换 key
                body = e.read().decode("utf-8", "replace") if e.fp else ""
                if e.code in (401, 403, 429) or "error" in body:
                    raise VendorError(f"twelvedata HTTP {e.code}: {body[:120]}") from e
                logger.debug(f"twelvedata {code} HTTP {e.code}: {body[:80]}")
                record_error(f"twelvedata {code}: HTTP {e.code}")
            except Exception as e:
                logger.debug(f"twelvedata {code} 失败: {e}")
                record_error(f"twelvedata {code}: {type(e).__name__}: {e}")
        return out

    @staticmethod
    def _to_td_symbol(s: Symbol) -> str:
        if s.market == Market.HK:
            return f"{int(s.code):04d}.HK" if s.code.isdigit() else f"{s.code}.HK"
        return s.code

    @staticmethod
    def _parse(raw: str, s: Symbol) -> Quote | None:
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            return None
        # 错误: {"status":"error","message":"...", "code":401/429}
        if isinstance(d, dict) and d.get("status") == "error":
            msg = d.get("message", "")
            raise VendorError(f"twelvedata error: {msg}")
        if not d or d.get("symbol") is None:
            return None
        try:
            price = float(d.get("close") or 0)
            prev = float(d.get("previous_close") or 0)
            chg = float(d.get("change") or 0)
            pct = float(d.get("percent_change") or 0)
            return Quote(
                symbol=s.code,
                market=s.market.value,
                name=d.get("name", s.code),
                current_price=price,
                prev_close=prev or None,
                open_price=float(d.get("open") or 0) or None,
                high_price=float(d.get("high") or 0) or None,
                low_price=float(d.get("low") or 0) or None,
                change_amount=chg or None,
                change_pct=pct or None,
                volume=float(d.get("volume") or 0) or None,
            )
        except (ValueError, TypeError) as e:
            logger.debug(f"twelvedata 解析失败: {e}")
            return None
