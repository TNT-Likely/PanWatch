"""腾讯行情 vendor(HTTP,GBK)。移植自 akshare_collector._parse_tencent_line/_fetch_tencent_quotes。"""

from __future__ import annotations

import logging

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import Quote
from marketdata.vendors.base import QuoteVendor

logger = logging.getLogger(__name__)

_URL = "http://qt.gtimg.cn/q="
_HOST = "qt.gtimg.cn"
_MIN_INTERVAL_S = 0.15


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


def _parse_line(line: str, market: str) -> Quote | None:
    if '=""' in line or not line.strip():
        return None
    try:
        _, value = line.split('="', 1)
        parts = value.rstrip('";').split("~")
        if len(parts) < 35:
            return None

        turnover = 0.0
        if "/" in str(parts[35]):
            tp = parts[35].split("/")
            if len(tp) >= 3:
                turnover = _to_float(tp[2]) or 0.0

        symbol = parts[2]
        if "." in symbol and not symbol.startswith("."):
            symbol = symbol.split(".")[0]

        turnover_rate = _to_float(parts[38]) if len(parts) > 39 else None
        pe_ratio = _to_float(parts[39]) if len(parts) > 39 else None
        circulating = _to_float(parts[44]) if len(parts) > 45 else None
        total = _to_float(parts[45]) if len(parts) > 45 else None
        volume_ratio = _to_float(parts[49]) if len(parts) > 49 else None

        return Quote(
            symbol=symbol,
            market=market,
            name=parts[1],
            current_price=float(parts[3] or 0),
            prev_close=float(parts[4] or 0),
            open_price=float(parts[5] or 0),
            volume=float(parts[6] or 0),
            change_amount=float(parts[31] or 0),
            change_pct=float(parts[32] or 0),
            high_price=float(parts[33] or 0),
            low_price=float(parts[34] or 0),
            turnover=turnover,
            turnover_rate=turnover_rate,
            volume_ratio=volume_ratio,
            pe_ratio=pe_ratio,
            circulating_market_value=circulating,
            total_market_value=total,
        )
    except (ValueError, IndexError) as e:
        logger.debug(f"解析腾讯行情失败: {e}")
        return None


def _fetch_lines(tencent_symbols: list[str]) -> list[str]:
    """按原始腾讯符号批量拉取响应,GBK 解码后按 ';' 切分为行。tencent quote / index 共用取数核。"""
    if not tencent_symbols:
        return []
    codes = ",".join(tencent_symbols)
    content = market_get(
        _URL + codes,
        host_key=_HOST,
        min_interval_s=_MIN_INTERVAL_S,
        timeout=10,
        retries=2,
        parse="content",
        log_label="腾讯报价",
    )
    if not content:
        return []
    text = content.decode("gbk", errors="ignore") if isinstance(content, (bytes, bytearray)) else str(content)
    return text.strip().split(";")


def fetch_raw(tencent_symbols: list[str]) -> list[dict]:
    """按原始腾讯符号(sh000001/hkHSI/usDJI…)取行情,不经 Symbol.parse。

    供指数等显式符号场景复用(指数代码与个股代码可能撞号,如 000001 既是平安银行又是上证指数)。
    返回 dict 列表:symbol/name/current_price/change_pct/change_amount/prev_close/volume/turnover。
    """
    out: list[dict] = []
    for line in _fetch_lines(tencent_symbols):
        q = _parse_line(line, "")
        if q and q.current_price > 0:
            out.append({
                "symbol": q.symbol,
                "name": q.name,
                "current_price": q.current_price,
                "change_pct": q.change_pct,
                "change_amount": q.change_amount,
                "prev_close": q.prev_close,
                "volume": q.volume,
                "turnover": q.turnover,
            })
    return out


class TencentQuoteVendor(QuoteVendor):
    name = "tencent"
    supports_markets = {"CN", "HK", "US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        if not symbols:
            return []
        market = symbols[0].market.value
        codes = [s.to_tencent() for s in symbols]
        out: list[Quote] = []
        for line in _fetch_lines(codes):
            q = _parse_line(line, market)
            if q and q.current_price > 0:
                out.append(q)
        return out
