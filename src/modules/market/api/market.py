"""市场指数 API - 公共数据，无需认证"""
import asyncio
import logging
import time
from fastapi import APIRouter

from src.platform.marketdata.collectors.kline_collector import get_index_klines
from src.platform.marketdata.models import MarketCode
from src.platform.marketdata.taiwan_index import twse_index as _twse_index

logger = logging.getLogger(__name__)
router = APIRouter()


def get_market_data():
    """惰性 import,避免包未装/循环 import 影响本模块加载。"""
    from src.platform.marketdata.marketdata_client import get_market_data as _g

    return _g()

# 主要市场指数配置
# response_symbol: 腾讯 API 返回的 symbol（用于匹配）
MARKET_INDICES = [
    # 美股指數 (騰訊返回的 symbol 帶點號字首: .IXIC, .DJI)
    {"symbol": "IXIC", "name": "納斯達克", "market": "US", "tencent_symbol": "usIXIC", "response_symbol": ".IXIC"},
    {"symbol": "DJI", "name": "道瓊斯", "market": "US", "tencent_symbol": "usDJI", "response_symbol": ".DJI"},
]



# 指數響應記憶體快取:60s(行情價格要新鮮)。
_INDICES_CACHE: dict[str, tuple[float, list[dict]]] = {}
_INDICES_CACHE_TTL_S = 60

# spark(近20日收盘)独立缓存:日线一天才变,30 分钟足够新鲜。
# 没有它,响应缓存每 60s 过期就要重付一轮 6×指数K线(部分环境东财先失败再腾讯兜底,
# 串行约 4s)——这曾是首页快车道最大的延迟来源。空结果也缓存(坏源别反复重拉)。
_SPARK_CACHE: dict[str, tuple[float, list[float]]] = {}
_SPARK_TTL_S = 1800


def clear_indices_cache() -> None:
    """清空指数响应/spark 缓存(测试隔离用)。"""
    _INDICES_CACHE.clear()
    _SPARK_CACHE.clear()


def _spark_for(idx: dict) -> list[float]:
    """近 20 日收盘价,供首页指数走势 sparkline 用(带 30min 独立缓存)。

    fail-soft:市场码非法/取数异常/无映射一律吞掉,返回空列表,绝不影响 quote 主体。
    """
    now = time.time()
    hit = _SPARK_CACHE.get(idx["symbol"])
    if hit and now - hit[0] < _SPARK_TTL_S:
        return hit[1]
    try:
        market_code = MarketCode(idx["market"])
        klines = get_index_klines(idx["symbol"], market_code, days=20)
        spark = [k.close for k in klines] if klines else []
    except Exception as e:
        logger.debug(f"指数 spark 获取失败 {idx['symbol']}: {e}")
        spark = []
    _SPARK_CACHE[idx["symbol"]] = (now, spark)
    return spark


@router.get("/indices")
async def get_market_indices():
    """获取主要市场指数（公共数据，无需认证）"""
    now = time.time()
    cached = _INDICES_CACHE.get("indices")
    if cached and now - cached[0] < _INDICES_CACHE_TTL_S:
        return cached[1]

    tencent_symbols = [idx["tencent_symbol"] for idx in MARKET_INDICES]

    try:
        quotes = get_market_data().index_quotes(tencent_symbols)
    except Exception as e:
        logger.error(f"獲取市場指數失敗: {e}")
        quotes = []

    # 构建 response_symbol -> quote 映射
    quote_map = {}
    for q in quotes:
        quote_map[q["symbol"]] = q

    # spark 并行取(缓存未过期时零成本;冷启动=最慢单个≈1s,而非 6 个串行累加)
    sparks = await asyncio.gather(
        *[asyncio.to_thread(_spark_for, idx) for idx in MARKET_INDICES],
        return_exceptions=True,
    )
    spark_map = {
        idx["symbol"]: (sp if isinstance(sp, list) else [])
        for idx, sp in zip(MARKET_INDICES, sparks)
    }

    result = []
    for idx in MARKET_INDICES:
        # 使用 response_symbol 匹配
        quote = quote_map.get(idx["response_symbol"])
        spark = spark_map.get(idx["symbol"], [])

        if quote:
            result.append({
                "symbol": idx["symbol"],
                "name": idx["name"],
                "market": idx["market"],
                "current_price": quote["current_price"],
                "change_pct": quote["change_pct"],
                "change_amount": quote["change_amount"],
                "prev_close": quote["prev_close"],
                "spark": spark,
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
                "spark": spark,
            })

    taiwan = await asyncio.to_thread(_twse_index)
    if taiwan:
        result.insert(0, taiwan)

    _INDICES_CACHE["indices"] = (now, result)
    return result
