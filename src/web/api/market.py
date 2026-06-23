"""市场指数 API - 公共数据，无需认证"""
import logging
from typing import Any

from fastapi import APIRouter

from src.collectors.akshare_collector import _fetch_tencent_quotes
from src.collectors.market_http import market_get

logger = logging.getLogger(__name__)
router = APIRouter()

# 主要市场指数配置
# response_symbol: 腾讯 API 返回的 symbol（用于匹配）
# eastmoney_secid: 东财全球指数 secid（腾讯无覆盖时使用，如美元指数）
MARKET_INDICES: list[dict[str, str]] = [
    # A股指数
    {"symbol": "000001", "name": "上证指数", "market": "CN", "tencent_symbol": "sh000001", "response_symbol": "000001"},
    {"symbol": "399001", "name": "深证成指", "market": "CN", "tencent_symbol": "sz399001", "response_symbol": "399001"},
    {"symbol": "399006", "name": "创业板指", "market": "CN", "tencent_symbol": "sz399006", "response_symbol": "399006"},
    {"symbol": "000300", "name": "沪深300", "market": "CN", "tencent_symbol": "sh000300", "response_symbol": "000300"},
    # 港股指数
    {"symbol": "HSI", "name": "恒生指数", "market": "HK", "tencent_symbol": "hkHSI", "response_symbol": "HSI"},
    {"symbol": "HSTECH", "name": "恒生科技", "market": "HK", "tencent_symbol": "hkHSTECH", "response_symbol": "HSTECH"},
    # 美股指数 (腾讯返回的 symbol 带点号前缀: .IXIC, .DJI)
    {"symbol": "INX", "name": "标普500", "market": "US", "tencent_symbol": "usINX", "response_symbol": ".INX"},
    {"symbol": "NDX", "name": "纳斯达克100", "market": "US", "tencent_symbol": "usNDX", "response_symbol": ".NDX"},
    {"symbol": "IXIC", "name": "纳斯达克", "market": "US", "tencent_symbol": "usIXIC", "response_symbol": ".IXIC"},
    {"symbol": "DJI", "name": "道琼斯", "market": "US", "tencent_symbol": "usDJI", "response_symbol": ".DJI"},
    # 全球 / 外汇（腾讯无覆盖，走东财）
    {"symbol": "UDI", "name": "美元指数", "market": "GLOBAL", "eastmoney_secid": "100.UDI"},
]


def _empty_index(idx: dict[str, str]) -> dict[str, Any]:
    return {
        "symbol": idx["symbol"],
        "name": idx["name"],
        "market": idx["market"],
        "current_price": None,
        "change_pct": None,
        "change_amount": None,
        "prev_close": None,
    }


def _quote_payload(idx: dict[str, str], quote: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": idx["symbol"],
        "name": idx["name"],
        "market": idx["market"],
        "current_price": quote.get("current_price"),
        "change_pct": quote.get("change_pct"),
        "change_amount": quote.get("change_amount"),
        "prev_close": quote.get("prev_close"),
    }


def _fetch_eastmoney_index(secid: str) -> dict[str, float] | None:
    """东财全球指数单条行情（fail-soft）。"""
    try:
        data = market_get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            host_key="push2.eastmoney.com",
            params={"secid": secid, "fields": "f43,f169,f170"},
            parse="json",
            min_interval_s=0.2,
        )
        row = (data or {}).get("data") if isinstance(data, dict) else None
        if not row:
            return None

        def _scaled(raw: Any, default: float | None = None) -> float | None:
            if raw in (None, "", "-"):
                return default
            try:
                return float(raw) / 100.0
            except (TypeError, ValueError):
                return default

        current_price = _scaled(row.get("f43"))
        if current_price is None:
            return None
        change_amount = _scaled(row.get("f169"), 0.0)
        change_pct = _scaled(row.get("f170"))
        prev_close = current_price - change_amount if change_amount is not None else None
        return {
            "current_price": current_price,
            "change_pct": change_pct,
            "change_amount": change_amount,
            "prev_close": prev_close,
        }
    except Exception as e:
        logger.debug("东财指数 %s 获取失败: %s", secid, e)
        return None


@router.get("/indices")
async def get_market_indices():
    """获取主要市场指数（公共数据，无需认证）"""
    tencent_indices = [idx for idx in MARKET_INDICES if idx.get("tencent_symbol")]
    eastmoney_indices = [idx for idx in MARKET_INDICES if idx.get("eastmoney_secid")]

    quote_map: dict[str, dict[str, Any]] = {}
    if tencent_indices:
        tencent_symbols = [idx["tencent_symbol"] for idx in tencent_indices]
        try:
            for q in _fetch_tencent_quotes(tencent_symbols):
                quote_map[q["symbol"]] = q
        except Exception as e:
            logger.error("获取市场指数失败: %s", e)

    eastmoney_map: dict[str, dict[str, float]] = {}
    for idx in eastmoney_indices:
        secid = idx["eastmoney_secid"]
        quote = _fetch_eastmoney_index(secid)
        if quote:
            eastmoney_map[secid] = quote

    result: list[dict[str, Any]] = []
    for idx in MARKET_INDICES:
        if idx.get("eastmoney_secid"):
            quote = eastmoney_map.get(idx["eastmoney_secid"])
            result.append(_quote_payload(idx, quote) if quote else _empty_index(idx))
            continue

        quote = quote_map.get(idx.get("response_symbol", ""))
        result.append(_quote_payload(idx, quote) if quote else _empty_index(idx))

    return result
