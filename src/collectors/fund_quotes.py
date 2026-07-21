"""基金实时行情采集（天天基金估值 API）"""

from __future__ import annotations

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# 天天基金实时估值 API
FUND_GZ_URL = "http://fundgz.1234567.com.cn/js/{code}.js"


def _parse_fund_gz_line(raw: str) -> dict | None:
    """解析天天基金估值接口返回: jsonpgz({...});"""
    text = (raw or "").strip()
    if not text:
        return None

    m = re.search(r"jsonpgz\((.*)\)\s*;?\s*$", text)
    if not m:
        return None

    try:
        data = json.loads(m.group(1))
    except Exception:
        return None

    fundcode = str(data.get("fundcode") or "").strip()
    if not fundcode:
        return None

    def _f(v: str | None) -> float | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        try:
            return float(s)
        except Exception:
            return None

    prev_close = _f(data.get("dwjz"))
    current_price = _f(data.get("gsz"))
    change_pct = _f(data.get("gszzl"))
    change_amount = None
    if prev_close is not None and current_price is not None:
        change_amount = current_price - prev_close

    return {
        "name": str(data.get("name") or ""),
        "symbol": fundcode,
        "current_price": current_price,
        "prev_close": prev_close,
        "open_price": None,
        "volume": None,
        "change_amount": change_amount,
        "change_pct": change_pct,
        "high_price": None,
        "low_price": None,
        "turnover": None,
        "turnover_rate": None,
        "pe_ratio": None,
        "circulating_market_value": None,
        "total_market_value": None,
        "gztime": str(data.get("gztime") or ""),
        "jzrq": str(data.get("jzrq") or ""),
    }


def _fetch_fund_quotes(symbols: list[str]) -> list[dict]:
    """批量获取基金实时估值（天天基金）。

    对于场外基金（没有实时估值），会回退到基金详情接口获取最新净值。
    """
    if not symbols:
        return []

    results: list[dict] = []
    # 去重后按输入顺序请求
    seen: set[str] = set()
    ordered = []
    for s in symbols:
        code = str(s).strip()
        if not code:
            continue
        if code in seen:
            continue
        seen.add(code)
        ordered.append(code)

    # 延迟导入避免循环引用
    from src.collectors.fund_collector import fetch_fund_performance

    with httpx.Client() as client:
        for code in ordered:
            try:
                resp = client.get(FUND_GZ_URL.format(code=code), timeout=10)
                parsed = _parse_fund_gz_line(resp.text)
                if parsed and parsed.get("current_price") is not None:
                    results.append(parsed)
                    continue
            except Exception as e:
                logger.debug(f"获取基金估值失败 {code}: {e}")

            # 估值接口无数据，尝试从基金详情获取最新净值
            try:
                perf = fetch_fund_performance(code)
                if perf and perf.get("points"):
                    latest = perf["points"][-1]
                    nav = latest.get("value")
                    ts = latest.get("ts")
                    jzrq = ""
                    if ts:
                        from datetime import datetime

                        jzrq = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                    if nav is not None:
                        results.append({
                            "symbol": code,
                            "name": perf.get("name") or code,
                            "current_price": None,  # 无实时估值
                            "prev_close": nav,  # 最新单位净值
                            "change": None,
                            "change_pct": None,
                            "volume": None,
                            "amount": None,
                            "turnover_rate": None,
                            "volume_ratio": None,
                            "total_market_value": None,
                            "gztime": "",
                            "jzrq": jzrq,
                        })
            except Exception as e:
                logger.debug(f"获取基金净值失败 {code}: {e}")

    return results
