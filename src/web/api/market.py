"""市场指数 API - 公共数据，无需认证"""
import asyncio
import logging
import time
from fastapi import APIRouter

from src.collectors.kline_collector import get_index_klines
from src.models.market import MarketCode

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

# 指数响应内存缓存:60s(行情价格要新鲜)。
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
        logger.error(f"获取市场指数失败: {e}")
        return []

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

    _INDICES_CACHE["indices"] = (now, result)
    return result


@router.get("/indices/{symbol}")
async def get_index_detail(symbol: str):
    """大盘指数详情: K线 + 实时行情 + 成交额(腾讯源,云服务器可用)。

    大盘资金流: 东财 fflow 在云服务器被断(502),用成交额/成交量趋势替代展示。
    """
    idx = next((i for i in MARKET_INDICES if i["symbol"] == symbol), None)
    if not idx:
        return {"error": f"未知指数 {symbol}", "symbol": symbol}

    # 1. 实时行情(腾讯) — to_thread 避免阻塞
    quote_data = None
    try:
        def _fetch_quote():
            quotes = get_market_data().index_quotes([idx["tencent_symbol"]])
            for q in quotes:
                if q["symbol"] == idx["response_symbol"]:
                    return {
                        "current_price": q["current_price"],
                        "change_pct": q["change_pct"],
                        "change_amount": q["change_amount"],
                        "prev_close": q["prev_close"],
                        "open": q.get("open"),
                        "high": q.get("high"),
                        "low": q.get("low"),
                        "volume": q.get("volume"),
                        "amount": q.get("amount"),
                    }
            return None

        quote_data = await asyncio.to_thread(_fetch_quote)
    except Exception as e:
        logger.warning(f"指数详情行情失败 {symbol}: {e}")

    # 2. 日K线(腾讯,120天) — 同步 requests 用 to_thread 避免阻塞事件循环
    klines = []
    try:
        import requests as _req

        if idx["market"] == "CN":
            tencent_code = idx["tencent_symbol"]
        elif idx["market"] == "HK":
            tencent_code = "hkHSI"
        else:
            tencent_code = {"IXIC": "usIXIC", "DJI": "usDJI"}.get(symbol, "")

        def _fetch_tencent_kline(code: str) -> list:
            r = _req.get(
                f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,120,qfq",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            if r.status_code != 200:
                return []
            d = r.json()
            data = (d.get("data") or {}).get(code) or {}
            bars = data.get("day") or data.get("qfqday") or []
            return [
                {
                    "date": b[0],
                    "open": float(b[1]),
                    "close": float(b[2]),
                    "high": float(b[3]),
                    "low": float(b[4]),
                    "volume": float(b[5]) if len(b) > 5 else 0,
                }
                for b in bars
            ]

        if tencent_code:
            klines = await asyncio.to_thread(_fetch_tencent_kline, tencent_code)
    except Exception as e:
        logger.warning(f"指数详情K线失败 {symbol}: {e}")

    # 3. 成交额趋势(资金流替代: 近20日成交额)
    amount_trend = [
        {"date": k["date"], "amount": k["volume"] * (k["close"] + k["open"]) / 2 / 1e8}
        for k in klines[-20:]
    ]

    return {
        "symbol": idx["symbol"],
        "name": idx["name"],
        "market": idx["market"],
        "quote": quote_data,
        "klines": klines,
        "amount_trend": amount_trend,
        "note": "大盘资金流:东财源在云服务器不可用(502),以成交额趋势替代",
    }
