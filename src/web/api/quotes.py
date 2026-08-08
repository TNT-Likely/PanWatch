import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.core.marketdata_client import md_quote_rows
from src.models.market import MarketCode

router = APIRouter()
logger = logging.getLogger(__name__)


class QuoteItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")


class QuoteBatchRequest(BaseModel):
    items: list[QuoteItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"不支持的市场: {market}")


def _quote_to_response(symbol: str, market: MarketCode, quote: dict | None) -> dict:
    if not quote:
        return {
            "symbol": symbol,
            "market": market.value,
            "name": None,
            "current_price": None,
            "change_pct": None,
            "change_amount": None,
            "prev_close": None,
            "open_price": None,
            "high_price": None,
            "low_price": None,
            "volume": None,
            "turnover": None,
            "turnover_rate": None,
            "volume_ratio": None,
            "pe_ratio": None,
            "total_market_value": None,
            "circulating_market_value": None,
        }

    return {
        "symbol": symbol,
        "market": market.value,
        "name": quote.get("name"),
        "current_price": quote.get("current_price"),
        "change_pct": quote.get("change_pct"),
        "change_amount": quote.get("change_amount"),
        "prev_close": quote.get("prev_close"),
        "open_price": quote.get("open_price"),
        "high_price": quote.get("high_price"),
        "low_price": quote.get("low_price"),
        "volume": quote.get("volume"),
        "turnover": quote.get("turnover"),
        "turnover_rate": quote.get("turnover_rate"),
        "volume_ratio": quote.get("volume_ratio"),
        "pe_ratio": quote.get("pe_ratio"),
        "total_market_value": quote.get("total_market_value"),
        "circulating_market_value": quote.get("circulating_market_value"),
    }


@router.get("/{symbol}")
async def get_quote(symbol: str, market: str = "CN"):
    """获取单只股票实时行情"""
    market_code = _parse_market(market)
    rows = await asyncio.to_thread(md_quote_rows, [symbol], market_code.value)
    if not rows:
        raise HTTPException(404, "行情不存在")
    quote_map = {item.get("symbol"): item for item in rows}
    quote = quote_map.get(symbol)
    if not quote:
        raise HTTPException(404, "行情不存在")
    return _quote_to_response(symbol, market_code, quote)


@router.post("/batch")
async def get_quotes_batch(payload: QuoteBatchRequest):
    """批量获取股票实时行情"""
    if not payload.items:
        return []

    market_items: dict[MarketCode, list[str]] = {}
    for item in payload.items:
        market_code = _parse_market(item.market)
        market_items.setdefault(market_code, []).append(item.symbol)

    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        rows = await asyncio.to_thread(md_quote_rows, symbols, market_code.value)
        quotes_by_market[market_code] = {item.get("symbol"): item for item in rows}

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        quote = quotes_by_market.get(market_code, {}).get(item.symbol)
        results.append(_quote_to_response(item.symbol, market_code, quote))

    return results


# 公司简介内存缓存(避免每次详情页都调 zhitu 耗时 1.8s)
_COMPANY_CACHE: dict = {}  # {(symbol, market): (ts, payload)}
_COMPANY_CACHE_TTL = 3600  # 1 小时


@router.get("/{symbol}/company")
async def get_company_info(symbol: str, market: str = "CN"):
    """获取公司基本信息(主营/简介/上市日期/行业等)。

    数据源: zhitu /gs/gsjj/{code}(公司简介,含 bscope主营/desc简介/ldate上市日期/idea概念)。
    缓存: 模块级内存缓存 1 小时,避免每次点详情页都调 zhitu(单次 1.8s)。
    注意: 必须定义在 /{symbol} 之后(FastAPI 按定义顺序匹配,先定义会被通配吞掉)。
    """
    market_code = _parse_market(market)
    if market_code != MarketCode.CN:
        return {"symbol": symbol, "market": market, "name": None, "industry": None,
                "area": None, "market_board": None, "list_status": None, "note": "仅A股支持公司简介"}

    # 命中缓存直接返回
    import time as _time
    cache_key = (symbol, market_code.value)
    cached = _COMPANY_CACHE.get(cache_key)
    if cached and (_time.time() - cached[0]) < _COMPANY_CACHE_TTL:
        return cached[1]

    try:
        import urllib.parse
        import urllib.request

        # token 优先级: 多 key 池化(pick_zhitu_token 轮换) > 单值 DB > env > 默认
        token = ""
        try:
            from marketdata.vendors.zhitu import pick_zhitu_token
            token = pick_zhitu_token()
        except Exception:
            pass
        if not token:
            try:
                from src.web.database import SessionLocal
                from src.web.models import AppSettings
                db = SessionLocal()
                row = db.query(AppSettings).filter(AppSettings.key == "zhitu_token").first()
                token = (row.value if row and row.value and row.value != "********" else "") or ""
                db.close()
            except Exception:
                pass
        if not token:
            import os
            token = os.environ.get("ZHITU_TOKEN", "E0E16C43-9272-4DAB-800C-178694F2D4B1")

        url = f"https://api.zhituapi.com/hs/gs/gsjj/{symbol}?token={token}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore"))
        if not isinstance(raw, dict) or not raw.get("name"):
            payload = {"symbol": symbol, "market": market, "name": None, "note": "未查到公司信息"}
        else:
            payload = {
                "symbol": symbol,
                "market": market,
                "name": raw.get("name"),
                "ename": raw.get("ename"),
                "industry": raw.get("instype"),
                "area": raw.get("addr", "").split(" ")[0][:30] if raw.get("addr") else None,
                "market_board": raw.get("market"),
                "list_status": raw.get("organ"),
                "list_date": raw.get("ldate"),
                "reg_capital": raw.get("rprice"),
                "issuer": raw.get("principal"),
                "secretary": raw.get("secre"),
                "phone": raw.get("phone"),
                "website": raw.get("site"),
                "address": raw.get("addr"),
                "bscope": raw.get("bscope"),
                "desc": raw.get("desc"),
                "concepts": raw.get("idea"),
                "note": None,
            }
        # 写缓存
        _COMPANY_CACHE[cache_key] = (_time.time(), payload)
        return payload
    except Exception as e:
        logger.error(f"公司信息获取失败 {symbol}: {e}")


# ── 分时走势(腾讯实时, 盘中) ─────────────────────────────────────────────
_MINUTE_CACHE: dict = {}  # {symbol_market: (ts, points)}
_MINUTE_TTL = 15.0  # 分时变动快, 短缓存


def _tencent_minute(symbol: str, market: str) -> list[dict] | None:
    """腾讯分时接口(https://web.ifzq.gtimg.cn)。返回 [{t, price, avg, volume}]。"""
    prefix = {"CN": "sh" if symbol.startswith(("6", "9")) else "sz",
              "HK": "hk", "US": ""}.get(market, "sh")
    code = f"{prefix}{symbol}" if market != "US" else symbol
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json as _json
            d = _json.load(resp)
        data = (d.get("data") or {}).get(code, {}).get("data", {}).get("data")
        if not data:
            return None
        # 字段: "0930 1308.66 173 22639818.00" = 时间 价格 累计量(手) 累计额(元)
        # ⚠️ 第3/4列本身已经是"当日累计"值, 不可再累加(旧版 cum_vol += vol 是 bug,
        #    会把累计量当增量二次累加, 导致均价分母膨胀、均价线整体失真)
        points = []
        prev_cum_vol = 0.0
        for row in data:
            parts = row.split()
            if len(parts) < 4:
                continue
            t, price = parts[0], float(parts[1])
            cum_vol, cum_amt = float(parts[2]), float(parts[3])  # 累计量(手) / 累计额(元)
            # 均价 = 累计成交额 / 累计成交股数 (1手=100股)
            avg = (cum_amt / (cum_vol * 100.0)) if cum_vol > 0 else price
            bar_vol = max(cum_vol - prev_cum_vol, 0.0)  # 本分钟增量成交量(手)
            prev_cum_vol = cum_vol
            points.append({"t": t, "price": price, "avg": round(avg, 2), "volume": int(bar_vol)})
        return points
    except Exception as e:
        logger.debug(f"腾讯分时失败 {symbol}: {e}")
        return None


@router.get("/minute")
async def get_minute(symbol: str, market: str = "CN"):
    """分时走势(盘中实时)。腾讯优先, 失败返回空。"""
    cache_key = f"{market}:{symbol}"
    cached = _MINUTE_CACHE.get(cache_key)
    if cached and (_time.time() - cached[0]) < _MINUTE_TTL:
        return {"symbol": symbol, "market": market, "points": cached[1]}
    points = _tencent_minute(symbol, market)
    if points is None:
        points = []
    _MINUTE_CACHE[cache_key] = (_time.time(), points)
    return {"symbol": symbol, "market": market, "points": points}
