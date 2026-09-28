"""腾讯行情 vendor(HTTP,GBK)。移植自 akshare_collector._parse_tencent_line/_fetch_tencent_quotes。

三市场字段布局互不相同（2026-09-28 qt.gtimg.cn 实测取证）：
- CN: 35=「价/量/额」复合(额为元)、38=换手%、39=PE、44=流通市值(亿)、45=总市值(亿)、49=量比
- HK: 35 无复合结构、37=成交额(元)、无换手率字段(38 恒 0)、39=PE、44/45=市值(亿 HKD)、49 非量比
- US: 变长布局(实测 67/70 字段,30 位后整体漂移)——以字面量锚 "USD" 的相对偏移取
      成交额(+2,USD)/PE(+4)/流通市值(+9,亿 USD)/总市值(+10,亿 USD);涨跌由现价/昨收算术推导
      (Apple 双源核验：$339.33→$4.98T，折算收盘价吻合，差 0.6% 为回购缩股)；高低位无稳定锚置 None
"""

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


def _derived_turnover_rate(volume: float, price: float, circulating_mv_yi: float | None) -> float | None:
    """腾讯 HK/US 行情无换手率字段,由流通市值(亿,本币)推导:成交量/流通股数*100。

    口径为流通市值推算的流通股数,非交易所自由流通口径;数据不足时返回 None(绝不造 0)。
    """
    if not volume or not price or not circulating_mv_yi:
        return None
    float_shares = circulating_mv_yi * 1e8 / price
    if float_shares <= 0:
        return None
    return round(volume / float_shares * 100, 4)


def _parse_cn_fields(parts: list[str], price: float, volume: float) -> dict:
    turnover = 0.0
    if "/" in str(parts[35]):
        tp = parts[35].split("/")
        if len(tp) >= 3:
            turnover = _to_float(tp[2]) or 0.0

    turnover_rate = _to_float(parts[38]) if len(parts) > 39 else None
    pe_ratio = _to_float(parts[39]) if len(parts) > 39 else None
    circulating = _to_float(parts[44]) if len(parts) > 45 else None
    total = _to_float(parts[45]) if len(parts) > 45 else None
    volume_ratio = _to_float(parts[49]) if len(parts) > 49 else None
    return {
        "turnover": turnover,
        "turnover_rate": turnover_rate,
        "pe_ratio": pe_ratio,
        "circulating_market_value": circulating,
        "total_market_value": total,
        "volume_ratio": volume_ratio,
        "change_amount": float(parts[31] or 0),
        "change_pct": float(parts[32] or 0),
        "high_price": float(parts[33] or 0),
        "low_price": float(parts[34] or 0),
        "prev_close": float(parts[4] or 0),
        "open_price": float(parts[5] or 0),
    }


def _parse_hk_fields(parts: list[str], price: float, volume: float) -> dict:
    turnover = _to_float(parts[37]) or 0.0  # 37=成交额(元);35 无「价/量/额」复合结构
    pe_ratio = _to_float(parts[39]) if len(parts) > 39 else None
    circulating = _to_float(parts[44]) if len(parts) > 45 else None
    total = _to_float(parts[45]) if len(parts) > 45 else None
    return {
        "turnover": turnover,
        # 腾讯 HK 无换手率字段(38 恒 0),由流通市值推导,缺失置 None
        "turnover_rate": _derived_turnover_rate(volume, price, circulating),
        "pe_ratio": pe_ratio,
        "circulating_market_value": circulating,
        "total_market_value": total,
        "volume_ratio": None,  # HK 布局 49 非量比
        "change_amount": float(parts[31] or 0),
        "change_pct": float(parts[32] or 0),
        "high_price": float(parts[33] or 0),
        "low_price": float(parts[34] or 0),
        "prev_close": float(parts[4] or 0),
        "open_price": float(parts[5] or 0),
    }


def _parse_us_fields(parts: list[str], price: float, volume: float) -> dict:
    # 美股为变长布局(实测 67/70 字段两种,盘前/盘中字段增删使 30 位之后整体移位,绝对索引不可靠)。
    # 仅接三类可靠来源:
    #   1) 头部固定区: 3=现价 4=昨收 5=今开 6=成交量(股) —— 三次取样稳定
    #   2) 字面量锚 "USD"(货币标记,唯一)之后的相对偏移: +2=成交额(USD) +4=PE +9=流通市值(亿 USD)
    #      +10=总市值(亿 USD) —— 两种布局交叉验证一致(Apple 市值另经外部报道双源核验)
    #   3) 算术推导: 涨跌额/涨跌% = 现价-昨收 / 现价/昨收-1(与取样值精确吻合)
    # 最高/最低位无稳定锚,置 None(宁缺勿错),后续取到稳定锚再接。
    prev_close = _to_float(parts[4]) or 0.0
    open_price = _to_float(parts[5]) or 0.0
    change_amount = round(price - prev_close, 4) if price and prev_close else 0.0
    change_pct = round((price / prev_close - 1) * 100, 4) if price and prev_close else 0.0

    usd = parts.index("USD") if "USD" in parts else -1

    def _at(offset: int) -> float | None:
        idx = usd + offset
        return _to_float(parts[idx]) if usd >= 0 and 0 <= idx < len(parts) else None

    circulating = _at(9)
    return {
        "turnover": _at(2) or 0.0,
        "turnover_rate": _derived_turnover_rate(volume, price, circulating),
        "pe_ratio": _at(4),
        "circulating_market_value": circulating,
        "total_market_value": _at(10),
        "volume_ratio": None,
        "change_amount": change_amount,
        "change_pct": change_pct,
        "high_price": None,  # 变长布局无稳定锚,置 None(前端显示 --)
        "low_price": None,
        "prev_close": prev_close,
        "open_price": open_price,
    }


_PARSERS = {"CN": _parse_cn_fields, "HK": _parse_hk_fields, "US": _parse_us_fields}


def _parse_line(line: str, market: str) -> Quote | None:
    if '=""' in line or not line.strip():
        return None
    try:
        _, value = line.split('="', 1)
        parts = value.rstrip('";').split("~")
        if len(parts) < 35:
            return None

        symbol = parts[2]
        if "." in symbol and not symbol.startswith("."):
            symbol = symbol.split(".")[0]

        price = float(parts[3] or 0)
        volume = float(parts[6] or 0)

        parser = _PARSERS.get(market, _parse_cn_fields)
        fields = parser(parts, price, volume)

        return Quote(
            symbol=symbol,
            market=market,
            name=parts[1],
            current_price=price,
            volume=volume,
            **fields,
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
