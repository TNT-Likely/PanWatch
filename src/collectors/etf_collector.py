"""场内 ETF 采集器 —— 基于 akshare(东财)。

数据口径:
- 实时行情+IOPV+折价率+规模: ``fund_etf_spot_em``(全量一次,15min TTL 缓存)
- 成分股: ``fund_portfolio_hold_em``(按季报,1h TTL)
- 净值历史: ``fund_etf_fund_info_em``(按需,缓存 1h)

设计要点:
- spot 是全量拉取(~15s,1500+ 只),故进程级缓存,15min 内不重拉。
- 所有取数异常返回 None/空列表,不拖垮调用方(详情弹窗/Agent)。
- 字段映射集中在 *_to_dict,akshare 改字段名只动这里。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import akshare as ak
import pandas as pd

from src.collectors.market_http import TTLCache

logger = logging.getLogger(__name__)

# 全量 spot 缓存:15min(盘中行情分钟级变化,盘后稳定)
_ETF_SPOT_CACHE: TTLCache = TTLCache(default_ttl_sec=900.0)
_SPOT_CACHE_KEY = "all"

# 成分股缓存:季报数据,1h 足够
_HOLDINGS_CACHE: TTLCache = TTLCache(default_ttl_sec=3600.0)

# 净值历史缓存:1h(日级数据)
_NAV_CACHE: TTLCache = TTLCache(default_ttl_sec=3600.0)

_DEFAULT_START = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
_DEFAULT_END = datetime.now().strftime("%Y%m%d")


def _safe_float(v: Any) -> float | None:
    """容错转 float,NaN/None/字符串均返回 None。"""
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        f = float(v)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _fetch_spot_all() -> pd.DataFrame:
    """拉取全量 ETF spot(带缓存)。失败返回空 DataFrame。"""
    cached = _ETF_SPOT_CACHE.get(_SPOT_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        df = ak.fund_etf_spot_em()
    except Exception as e:  # noqa: BLE001 - 采集层兜底,不区分异常类型
        logger.warning("ETF spot 拉取失败: %s", e)
        return pd.DataFrame()
    _ETF_SPOT_CACHE.set(_SPOT_CACHE_KEY, df)
    return df


def _spot_row_to_dict(row: pd.Series) -> dict[str, Any]:
    return {
        "symbol": str(row.get("代码", "")),
        "name": str(row.get("名称", "")),
        "price": _safe_float(row.get("最新价")),
        "iopv": _safe_float(row.get("IOPV实时估值")),
        # 东财"基金折价率"正值为溢价、负值为折价,统一为 premium_pct
        "premium_pct": _safe_float(row.get("基金折价率")),
        "change_pct": _safe_float(row.get("涨跌幅")),
        "turnover": _safe_float(row.get("成交额")),
        "total_value": _safe_float(row.get("总市值")),  # 基金规模
        "turnover_rate": _safe_float(row.get("换手率")),
        "volume": _safe_float(row.get("成交量")),
    }


def get_etf_spot(symbol: str) -> dict[str, Any] | None:
    """取单只 ETF 实时行情(含 IOPV/折价率/规模)。未命中返回 None。"""
    sym = (symbol or "").strip()
    if not sym:
        return None
    df = _fetch_spot_all()
    if df.empty:
        return None
    matched = df[df["代码"].astype(str) == sym]
    if matched.empty:
        return None
    return _spot_row_to_dict(matched.iloc[0])


def get_etf_holdings(symbol: str, top: int = 30) -> list[dict[str, Any]]:
    """取 ETF 成分股(取最近一份季报,按占净值降序,截断 top)。"""
    sym = (symbol or "").strip()
    cache_key = f"{sym}:{top}"
    cached = _HOLDINGS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    year = datetime.now().year
    holdings: list[dict[str, Any]] = []
    # 优先当年,失败回退上一年
    for y in (year, year - 1):
        try:
            df = ak.fund_portfolio_hold_em(symbol=sym, date=str(y))
        except Exception as e:  # noqa: BLE001
            logger.warning("ETF %s 成分股拉取失败(%s): %s", sym, y, e)
            continue
        if df is None or df.empty:
            continue
        if "股票代码" not in df.columns:
            continue
        # 取最近一份季报(季度列去重取最后一个)
        if "季度" in df.columns:
            latest_q = df["季度"].dropna().iloc[-1]
            df = df[df["季度"] == latest_q]
        rows = []
        for _, r in df.iterrows():
            weight = _safe_float(r.get("占净值比例"))
            if weight is None:
                continue
            rows.append(
                {
                    "symbol": str(r.get("股票代码", "")),
                    "name": str(r.get("股票名称", "")),
                    "weight_pct": weight,
                }
            )
        rows.sort(key=lambda x: x["weight_pct"], reverse=True)
        holdings = rows[:top]
        if holdings:
            break

    _HOLDINGS_CACHE.set(cache_key, holdings)
    return holdings


def get_etf_nav_history(symbol: str, days: int = 180) -> list[dict[str, Any]]:
    """取净值历史(单位净值+累计净值,按日期升序)。"""
    sym = (symbol or "").strip()
    cache_key = f"{sym}:{days}"
    cached = _NAV_CACHE.get(cache_key)
    if cached is not None:
        return cached

    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end = _DEFAULT_END
    try:
        df = ak.fund_etf_fund_info_em(fund=sym, start_date=start, end_date=end)
    except Exception as e:  # noqa: BLE001
        logger.warning("ETF %s 净值历史拉取失败: %s", sym, e)
        return []

    if df is None or df.empty:
        return []

    nav: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        d = r.get("净值日期")
        date_str = ""
        if isinstance(d, (pd.Timestamp, datetime)):
            date_str = pd.Timestamp(d).strftime("%Y-%m-%d")
        elif d is not None:
            date_str = str(d)
        nav.append(
            {
                "date": date_str,
                "unit_nav": _safe_float(r.get("单位净值")),
                "cum_nav": _safe_float(r.get("累计净值")),
                "change_pct": _safe_float(r.get("日增长率")),
            }
        )
    nav.sort(key=lambda x: x["date"])
    _NAV_CACHE.set(cache_key, nav)
    return nav


def get_etf_overview(symbol: str, top: int = 30, nav_days: int = 180) -> dict[str, Any]:
    """聚合 ETF 详情:spot + 成分股 + 净值历史。单次调用,各部分独立兜底。"""
    return {
        "symbol": (symbol or "").strip(),
        "spot": get_etf_spot(symbol),
        "holdings": get_etf_holdings(symbol, top=top),
        "nav_history": get_etf_nav_history(symbol, days=nav_days),
    }
