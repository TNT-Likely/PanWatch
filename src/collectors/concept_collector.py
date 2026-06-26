"""个股概念/板块标签采集（东财 push2 slist）。"""

from __future__ import annotations

import logging

from src.collectors.kline_collector import _eastmoney_secid
from src.collectors.market_http import TTLCache, market_get
from src.core.cn_symbol import is_cn_sh
from src.models.market import MarketCode

logger = logging.getLogger(__name__)

_CONCEPT_CACHE = TTLCache(default_ttl_sec=3600.0)
_EASTMONEY_HOST = "push2.eastmoney.com"
_EASTMONEY_MIN_INTERVAL_S = 0.35
_SLIST_URL = "https://push2.eastmoney.com/api/qt/slist/get"
_STOCK_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://quote.eastmoney.com",
}
_UT = "fa5fd1943c7b386f172d6893dbfba10b"
_MAX_TAGS = 30


def _normalize_tag(name: str) -> str:
    return (name or "").strip()


def _dedupe_tags(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        tag = _normalize_tag(raw)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= _MAX_TAGS:
            break
    return out


def _parse_slist_diff(data: dict | None) -> list[str]:
    diff = ((data or {}).get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    concepts: list[str] = []
    for item in diff:
        if not isinstance(item, dict):
            continue
        name = _normalize_tag(str(item.get("f14") or ""))
        if name:
            concepts.append(name)
    return concepts


def _fetch_industry(
    secid: str,
    *,
    symbol: str,
    timeout_s: float,
    proxy: str | None,
) -> str:
    """可选：拉取所属行业，用于从概念列表中剔除重复项。"""
    data = market_get(
        _STOCK_URL,
        host_key=_EASTMONEY_HOST,
        params={
            "secid": secid,
            "fields": "f127",
            "ut": _UT,
            "fltt": "2",
            "invt": "2",
        },
        headers=_HEADERS,
        min_interval_s=_EASTMONEY_MIN_INTERVAL_S,
        timeout=timeout_s,
        retries=1,
        parse="json",
        symbol=symbol,
        log_label="概念行业",
        verify=False,
        proxy=proxy,
    )
    if not data:
        return ""
    stock_data = data.get("data") or {}
    return _normalize_tag(str(stock_data.get("f127") or ""))


def fetch_cn_concept_tags(
    symbol: str,
    *,
    timeout_s: float = 8.0,
    proxy: str | None = None,
    verify_ssl: bool = False,
) -> list[str]:
    """拉取 A 股所属概念/板块标签（行业/概念/地域混合，东财 slist）。"""
    sym = (symbol or "").strip()
    if not sym or not sym.isdigit():
        return []

    cache_key = f"concept:{sym}"
    cached = _CONCEPT_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)

    secid = _eastmoney_secid(sym, MarketCode.CN)
    slist_data = market_get(
        _SLIST_URL,
        host_key=_EASTMONEY_HOST,
        params={
            "forcect": "1",
            "spt": "3",
            "fields": "f1,f12,f152,f3,f14,f128,f136",
            "pi": "0",
            "pz": "1000",
            "po": "1",
            "fid": "f3",
            "fid0": "f4003",
            "invt": "2",
            "secid": secid,
        },
        headers=_HEADERS,
        min_interval_s=_EASTMONEY_MIN_INTERVAL_S,
        timeout=timeout_s,
        retries=2,
        parse="json",
        symbol=sym,
        log_label="概念标签",
        verify=verify_ssl,
        proxy=proxy,
    )
    if not slist_data:
        return []

    industry = _fetch_industry(
        secid, symbol=sym, timeout_s=timeout_s, proxy=proxy
    )
    concepts = _parse_slist_diff(slist_data)
    if industry:
        concepts = [c for c in concepts if c != industry]
    tags = _dedupe_tags(concepts)
    _CONCEPT_CACHE.set(cache_key, tags)
    return tags


def can_auto_fetch_concept_tags(market: str) -> bool:
    return (market or "").strip().upper() == "CN"


def cn_market_code_for_symbol(symbol: str) -> str:
    return "1" if is_cn_sh(symbol) else "0"
