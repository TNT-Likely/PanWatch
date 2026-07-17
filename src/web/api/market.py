"""市场指数 API - 公共数据，无需认证"""
import logging
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()


def get_market_data():
    """惰性 import,避免包未装/循环 import 影响本模块加载。"""
    from src.core.marketdata_client import get_market_data as _g

    return _g()

# 主要市场指数配置
# response_symbol: 腾讯 API 返回的 symbol（用于匹配）
MARKET_INDICES = [
    # A股指数
    {"symbol": "000001", "name": "上证指数", "market": "CN", "tencent_symbol": "sh000001", "response_symbol": "000001"},
    {"symbol": "399001", "name": "深证成指", "market": "CN", "tencent_symbol": "sz399001", "response_symbol": "399001"},
    {"symbol": "399006", "name": "创业板指", "market": "CN", "tencent_symbol": "sz399006", "response_symbol": "399006"},
    # 港股指数
    {"symbol": "HSI", "name": "恒生指数", "market": "HK", "tencent_symbol": "hkHSI", "response_symbol": "HSI"},
    # 美股指数 (腾讯返回的 symbol 带点号前缀: .IXIC, .DJI)
    {"symbol": "IXIC", "name": "纳斯达克", "market": "US", "tencent_symbol": "usIXIC", "response_symbol": ".IXIC"},
    {"symbol": "DJI", "name": "道琼斯", "market": "US", "tencent_symbol": "usDJI", "response_symbol": ".DJI"},
]


@router.get("/indices")
async def get_market_indices():
    """获取主要市场指数（公共数据，无需认证）"""
    tencent_symbols = [idx["tencent_symbol"] for idx in MARKET_INDICES]

    try:
        quotes = get_market_data().index_quotes(tencent_symbols)
    except Exception as e:
        logger.error(f"获取市场指数失败: {e}")
        return []

    # 构建 response_symbol -> quote 映射
    quote_map = {}
    for q in quotes:
        quote_map[q["symbol"]] = q

    result = []
    for idx in MARKET_INDICES:
        # 使用 response_symbol 匹配
        quote = quote_map.get(idx["response_symbol"])

        if quote:
            result.append({
                "symbol": idx["symbol"],
                "name": idx["name"],
                "market": idx["market"],
                "current_price": quote["current_price"],
                "change_pct": quote["change_pct"],
                "change_amount": quote["change_amount"],
                "prev_close": quote["prev_close"],
            })
        else:
            # 即使没有行情也返回基本信息
            result.append({
                "symbol": idx["symbol"],
                "name": idx["name"],
                "market": idx["market"],
                "current_price": None,
                "change_pct": None,
                "change_amount": None,
                "prev_close": None,
            })

    return result
