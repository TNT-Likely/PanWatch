"""K 线 vendors:腾讯(全市场)/ Stooq(US)/ 东财(CN/HK)。移植自 PanWatch kline_collector 抓取核。"""
from __future__ import annotations

import json
import logging

from marketdata.http import market_get
from marketdata.symbol import Market, Symbol
from marketdata.types import Bar
from marketdata.vendors.base import KlineVendor

logger = logging.getLogger(__name__)

_TENCENT_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_STOOQ_URL = "https://stooq.com/q/d/l/"


def _days(config: dict, default: int = 60) -> int:
    try:
        return int(config.get("days") or default)
    except Exception:
        return default


class TencentKlineVendor(KlineVendor):
    name = "tencent"
    supports_markets = {"CN", "HK", "US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0]
        days = _days(config)
        tsym = sym.to_tencent()
        text = market_get(
            _TENCENT_URL, host_key="web.ifzq.gtimg.cn", min_interval_s=0.15,
            params={"param": f"{tsym},day,,,{days},qfq", "_var": "kline_dayqfq"},
            timeout=10, retries=2, parse="text", log_label="腾讯K线", symbol=sym.code,
        )
        if not text or "=" not in text:
            return []
        js = text.split("=", 1)[1].strip().rstrip(";")
        try:
            data = json.loads(js)
        except Exception:
            return []
        raw = data.get("data", {}) if isinstance(data, dict) else {}
        day = []
        if isinstance(raw, dict):
            sd = raw.get(tsym, {})
            if isinstance(sd, dict):
                day = sd.get("day") or sd.get("qfqday") or []
        elif isinstance(raw, list):
            day = raw
        out: list[Bar] = []
        for it in day or []:
            if len(it) >= 5:
                try:
                    out.append(Bar(date=it[0], open=float(it[1]), close=float(it[2]),
                                   high=float(it[3]), low=float(it[4]),
                                   volume=float(it[5]) if len(it) > 5 else 0.0))
                except Exception:
                    continue
        return out


class StooqKlineVendor(KlineVendor):
    name = "stooq"
    supports_markets = {"US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0].code.strip().lower()
        if not sym:
            return []
        text = market_get(
            _STOOQ_URL, host_key="stooq.com", params={"s": f"{sym}.us", "i": "d"},
            headers={"User-Agent": "PanWatch/1.0 (+https://github.com/)"},
            timeout=12, retries=2, parse="text", log_label="Stooq K线", symbol=sym,
        )
        if not text:
            return []
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) <= 1:
            return []
        out: list[Bar] = []
        for ln in lines[1:]:
            p = ln.split(",")
            if len(p) < 6 or not p[0] or p[0] == "Date":
                continue
            try:
                out.append(Bar(date=p[0], open=float(p[1]), close=float(p[4]),
                               high=float(p[2]), low=float(p[3]),
                               volume=float(p[5]) if p[5] else 0.0))
            except Exception:
                continue
        return out


def _em_secid(sym: Symbol) -> str:
    if sym.market == Market.HK:
        return f"116.{sym.code}"
    if sym.market == Market.US:
        return f"105.{sym.code}"
    from marketdata.symbol import _cn_exchange
    return f"{'1' if _cn_exchange(sym.code) == 'sh' else '0'}.{sym.code}"


def fetch_eastmoney_kline(secid: str, days: int) -> list[Bar]:
    """按显式 secid 取东财日K,不经个股 secid 推导规则(_em_secid)。

    供指数等显式符号场景复用(指数与个股 secid 前缀规则不同,必须显式映射)。
    """
    payload = market_get(
        _EASTMONEY_URL, host_key="push2his.eastmoney.com", min_interval_s=0.2,
        params={"secid": secid, "klt": "101", "fqt": "1",
                "lmt": str(min(max(int(days or 1), 1200), 20000)), "end": "20500101",
                "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b"},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        timeout=12, retries=1, parse="json", log_label="东财K线", symbol=secid,
    )
    raw = (payload or {}).get("data", {}).get("klines", []) if isinstance(payload, dict) else []
    out: list[Bar] = []
    for row in raw or []:
        p = str(row).split(",")
        if len(p) < 6:
            continue
        try:
            out.append(Bar(date=p[0], open=float(p[1]), close=float(p[2]),
                           high=float(p[3]), low=float(p[4]), volume=float(p[5])))
        except Exception:
            continue
    return out


class EastmoneyKlineVendor(KlineVendor):
    name = "eastmoney"
    supports_markets = {"CN", "HK"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0]
        if sym.market not in (Market.CN, Market.HK):
            return []
        days = _days(config)
        return fetch_eastmoney_kline(_em_secid(sym), days)
