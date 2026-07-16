"""新浪(Sina)US/HK 行情 vendor(HTTP,GBK)。免 key 免代理,作腾讯之后的 US/HK 备源。

端点:
- US: GET https://hq.sinajs.cn/list=gb_{code.lower()},多个逗号拼接。
- HK: GET https://hq.sinajs.cn/list=rt_hk{code},多个逗号拼接。
Header 必带 Referer + UA,响应 GBK 编码,多行 `var hq_str_XXX_yyy="...";`。
"""

from __future__ import annotations

import logging
import re

from marketdata.http import market_get
from marketdata.symbol import Market, Symbol
from marketdata.types import Quote
from marketdata.vendors.base import QuoteVendor

logger = logging.getLogger(__name__)

_URL = "https://hq.sinajs.cn/list="
_HOST = "hq.sinajs.cn"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_HEADERS = {"Referer": "https://finance.sina.com.cn/", "User-Agent": _UA}

_LINE_RE = re.compile(r'hq_str_(gb_|rt_hk)(\S+?)="(.*?)"')


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_us_line(code: str, content: str) -> Quote | None:
    parts = content.split(",")
    if len(parts) < 30:
        return None
    price = _to_float(parts[1]) or 0.0
    if price <= 0:
        return None
    return Quote(
        symbol=code.upper(),
        market="US",
        name=parts[0],
        current_price=price,
        prev_close=_to_float(parts[26]),
        open_price=_to_float(parts[5]),
        high_price=_to_float(parts[6]),
        low_price=_to_float(parts[7]),
        volume=_to_float(parts[10]),
        change_pct=_to_float(parts[2]),
        pe_ratio=_to_float(parts[14]),
    )


def _parse_hk_line(code: str, content: str) -> Quote | None:
    parts = content.split(",")
    if len(parts) < 15:
        return None
    price = _to_float(parts[6]) or 0.0
    if price <= 0:
        return None
    return Quote(
        symbol=code,
        market="HK",
        name=parts[1],
        current_price=price,
        open_price=_to_float(parts[2]),
        prev_close=_to_float(parts[3]),
        high_price=_to_float(parts[4]),
        low_price=_to_float(parts[5]),
        change_amount=_to_float(parts[7]),
        change_pct=_to_float(parts[8]),
        turnover=_to_float(parts[11]),
        volume=_to_float(parts[12]),
    )


class SinaQuoteVendor(QuoteVendor):
    name = "sina"
    supports_markets = {"US", "HK"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        if not symbols:
            return []
        market = symbols[0].market
        if market == Market.US:
            codes = [s.code for s in symbols]
            list_param = ",".join(f"gb_{c.lower()}" for c in codes)
        elif market == Market.HK:
            codes = [s.code for s in symbols]
            list_param = ",".join(f"rt_hk{c}" for c in codes)
        else:
            return []

        text = market_get(
            _URL + list_param,
            host_key=_HOST,
            headers=_HEADERS,
            parse="text",
            encoding="gbk",
            retries=2,
            timeout=8,
            min_interval_s=0.0,
            log_label="新浪报价",
        )
        if not text:
            return []

        out: list[Quote] = []
        for line in text.strip().splitlines():
            m = _LINE_RE.search(line)
            if not m:
                continue
            prefix, code, content = m.group(1), m.group(2), m.group(3)
            try:
                if prefix == "gb_":
                    q = _parse_us_line(code, content)
                else:
                    q = _parse_hk_line(code, content)
            except (ValueError, IndexError) as e:
                logger.debug(f"解析新浪行情失败: {e}")
                continue
            if q:
                out.append(q)
        return out
