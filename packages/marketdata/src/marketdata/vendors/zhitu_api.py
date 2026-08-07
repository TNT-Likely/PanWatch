"""智兔数服 API 统一客户端(全量能力封装)。

封装智兔数服(zhituapi.com)全部 HTTP 接口, token 走 pick_zhitu_token() 多 key 池化。
能力覆盖: K线/实时指数/竞价/涨停池/资金流/公司简介/财务/季报/年报分红/经营范围/
十大股东/股东变化/基金持股/业绩预告/公告/新股/概念板块。

注意: 这是底层 HTTP 客户端, 返回原始 dict; 具体解析成 Engine 标准类型在各 vendor 里做。
"""

import json
import logging
import urllib.parse
import urllib.request

from marketdata.vendors.zhitu import pick_zhitu_token

logger = logging.getLogger("marketdata.zhitu_api")

_ZHITU_BASE = "https://api.zhituapi.com/hs"


def _get(path: str, params: dict | None = None, timeout: float = 15.0) -> dict | list | None:
    """GET 智兔接口, 返回解析后的 dict/list; 失败返回 None。token 自动多 key 轮换。"""
    token = pick_zhitu_token()
    q = {"token": token, **(params or {})}
    url = f"{_ZHITU_BASE}{path}?{urllib.parse.urlencode(q)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"智兔请求失败 {path}: {type(e).__name__}: {e}")
        return None


# ---------- K线 ----------
def kline(code: str, level: str = "d", adj: str = "n", latest: int = 60) -> list | None:
    """K线。code 格式 000001.SZ / 600519.SH; level: d/w/m/y/5/15/30/60; adj: n/q/h。"""
    if level in ("5", "15", "30", "60"):
        adj = "n"
    data = _get(f"/latest/{code}/{level}/{adj}", {"lt": latest})
    return data if isinstance(data, list) else None


# ---------- 资金流(历史逐笔交易汇总) ----------
def capital_flow(code: str, start_date: str | None = None, end_date: str | None = None, latest: int = 30) -> list | None:
    """4维资金流(主买/主卖 × 特大/大/中/小)。zhitu 用 /history/transaction/{code}。"""
    params = {}
    if latest:
        params["lt"] = latest
    if start_date:
        params["st"] = start_date
    if end_date:
        params["et"] = end_date
    data = _get(f"/history/transaction/{code}", params)
    return data if isinstance(data, list) else None


# ---------- 公司基本面 ----------
def finance_main(code: str) -> dict | None:
    """财务主要(PE/PB/ROE/总市值等)。"""
    return _get(f"/gs/cwzy/{code}")


def quarterly_income(code: str) -> dict | None:
    """季报利润。"""
    return _get(f"/gs/jrjy/{code}")


def quarterly_cashflow(code: str) -> dict | None:
    """季报现金流。"""
    return _get(f"/gs/jxjl/{code}")


def business_scope(code: str) -> dict | None:
    """经营范围。"""
    return _get(f"/gs/jyfw/{code}")


def performance_forecast(code: str) -> dict | None:
    """业绩预告。"""
    return _get(f"/gs/ygjc/{code}")


# ---------- 股东 ----------
def top10_holders(code: str) -> dict | None:
    """十大股东。"""
    return _get(f"/gs/gdct/{code}")


def top_holder_changes(code: str) -> dict | None:
    """股东变化(户数变动)。"""
    return _get(f"/gs/gdbd/{code}")


def fund_holdings(code: str) -> dict | None:
    """基金持股(周更)。"""
    return _get(f"/gs/jjcg/{code}")


# ---------- 公告 ----------
def announcements(code: str) -> list | None:
    """累计公告。"""
    data = _get(f"/gs/announcements/{code}")
    return data if isinstance(data, list) else None


# ---------- 指数/竞价/涨停池/概念(市场级) ----------
def index_realtime() -> dict | None:
    """实时指数(大盘)。"""
    return _get("/zs/ss")


def auction_minute(date: str = "", latest: int = 60) -> list | None:
    """早盘竞价 9:15-9:25 分钟级。"""
    params = {"lt": latest} if latest else {}
    if date:
        params["dt"] = date
    data = _get("/zx/9d25", params)
    return data if isinstance(data, list) else None


def limit_up_pool(date: str = "") -> list | None:
    """涨停股池。"""
    params = {"dt": date} if date else {}
    data = _get("/zt/stock", params)
    return data if isinstance(data, list) else None


def concept_sectors() -> list | None:
    """概念板块列表。"""
    data = _get("/gn/list")
    return data if isinstance(data, list) else None


def stock_list() -> list | None:
    """沪深A股列表。"""
    data = _get("/stock/list")
    return data if isinstance(data, list) else None
