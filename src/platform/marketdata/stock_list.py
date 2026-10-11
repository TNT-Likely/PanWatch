"""股票标的清单的数据源适配器、项目级缓存与模糊搜索。

清单可被 API、任务调度和业务模块共同使用，因此属于市场数据平台，而不是
HTTP 层。缓存仍固定保存在项目根目录的 ``data/``，避免移动代码后悄然生成
另一份 ``src/data`` 缓存。
"""
import json
import os
import time
import logging
import concurrent.futures
import threading
from pathlib import Path

import httpx

from src.platform.marketdata.instruments import infer_asset_type

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_FILE = DATA_DIR / "stock_list_cache.json"
CACHE_TTL = 86400 * 7  # 7 days
CACHE_VERSION = 2  # Include listed ETFs; refresh pre-ETF catalogs once.

# 东方财富 A 股（使用 push2delay 域名，避免重定向）
EASTMONEY_URL = "http://80.push2delay.eastmoney.com/api/qt/clist/get"
EASTMONEY_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
    "fields": "f12,f14",
}

# 东方财富港股参数
EASTMONEY_HK_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2",  # 港股主板、创业板等
    "fields": "f12,f14",
}

# 东方财富美股参数
EASTMONEY_US_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:105,m:106,m:107",  # 美股 NYSE, NASDAQ, AMEX
    "fields": "f12,f14",
}

# 东方财富北交所参数（北证A股）
EASTMONEY_BJ_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0+t:81",  # 北交所
    "fields": "f12,f14",
}
PAGE_SIZE = 100

EASTMONEY_ETF_PARAMS = {
    **EASTMONEY_PARAMS,
    "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827",
}


_AKSHARE_LOCK = threading.Lock()
_AKSHARE_FUTURE = None


def _akshare_fallback():
    """Bound caller latency without executor shutdown joining a stuck provider.

    A single daemon worker also prevents repeated refreshes from accumulating
    blocked akshare calls. No database sessions are passed to this worker.
    """
    global _AKSHARE_FUTURE
    with _AKSHARE_LOCK:
        if _AKSHARE_FUTURE is not None and not _AKSHARE_FUTURE.done():
            return _AKSHARE_FUTURE
        future = concurrent.futures.Future()
        _AKSHARE_FUTURE = future
        def run():
            try:
                future.set_result(_fetch_from_akshare())
            except Exception as exc:
                future.set_exception(exc)
        threading.Thread(target=run, name="stock-list-akshare", daemon=True).start()
        return future


def _load_cache(*, allow_stale: bool = False) -> list[dict] | None:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if allow_stale or (
            data.get("version") == CACHE_VERSION
            and time.time() - data.get("ts", 0) < data.get("ttl", CACHE_TTL)
        ):
            return data["stocks"]
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _save_cache(stocks: list[dict], *, ttl: int = CACHE_TTL):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"version": CACHE_VERSION, "ts": time.time(), "ttl": ttl, "stocks": stocks}, f, ensure_ascii=False)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://quote.eastmoney.com/",
}


def _fetch_page(client: httpx.Client, page: int) -> list[dict]:
    """获取东方财富股票列表的单页"""
    params = {**EASTMONEY_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in items]


def _fetch_from_eastmoney() -> list[dict]:
    """东方财富 A 股列表（HTTP 分页并发获取）"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        # 第一页: 获取总数
        params = {**EASTMONEY_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        # 剩余页并发获取
        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_hk_page(client: httpx.Client, page: int) -> list[dict]:
    """获取东方财富港股列表的单页"""
    params = {**EASTMONEY_HK_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "HK"} for item in items]


def _fetch_hk_from_eastmoney() -> list[dict]:
    """东方财富港股列表"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        params = {**EASTMONEY_HK_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "HK"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_hk_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富港股第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_bj_page(client: httpx.Client, page: int) -> list[dict]:
    """获取东方财富北交所列表的单页"""
    params = {**EASTMONEY_BJ_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in items]


def _fetch_bj_from_eastmoney() -> list[dict]:
    """东方财富北交所列表（HTTP 分页并发获取）"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        # 第一页: 获取总数
        params = {**EASTMONEY_BJ_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        # 剩余页并发获取
        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_bj_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富北交所第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_us_page(client: httpx.Client, page: int) -> list[dict]:
    """获取东方财富美股列表的单页"""
    params = {**EASTMONEY_US_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "US"} for item in items]


def _fetch_us_from_eastmoney() -> list[dict]:
    """东方财富美股列表"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        params = {**EASTMONEY_US_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "US"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_us_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富美股第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_from_akshare() -> list[dict]:
    """akshare 数据源（备用，可能有 SSL 问题）"""
    import akshare as ak

    df = ak.stock_info_a_code_name()
    stocks = []
    for _, row in df.iterrows():
        stocks.append({
            "symbol": str(row["code"]),
            "name": str(row["name"]),
            "market": "CN",
        })
    return stocks


def _fetch_etf_from_eastmoney() -> list[dict]:
    """Fetch the provider's listed CN ETF boards, excluding OTC funds and LOFs."""
    def fetch_page(client: httpx.Client, page: int) -> dict:
        response = client.get(
            EASTMONEY_URL,
            params={**EASTMONEY_ETF_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)},
        )
        response.raise_for_status()
        return response.json().get("data") or {}

    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        first = fetch_page(client, 1)
        items = first.get("diff") or []
        pages = (int(first.get("total") or 0) + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            # A failed page fails this catalog so refresh can retain its old ETFs.
            for page in pool.map(lambda pn: fetch_page(client, pn), range(2, pages + 1)):
                items.extend(page.get("diff") or [])
    return [
        {"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN", "asset_type": "etf"}
        for item in items
    ]


def _fetch_etf_from_sina() -> list[dict]:
    """Independent CN ETF catalog fallback when Eastmoney is unavailable."""
    base = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center."
    with httpx.Client(headers={"Referer": "https://finance.sina.com.cn/"}, timeout=10) as client:
        response = client.get(base + "getHQNodeStockCount", params={"node": "etf_hq_fund"})
        response.raise_for_status()
        total = int(response.json())
        def fetch_page(page):
            response = client.get(base + "getHQNodeData", params={
                "node": "etf_hq_fund", "page": page, "num": PAGE_SIZE,
                "sort": "symbol", "asc": 1,
            })
            response.raise_for_status()
            items = response.json()
            if not isinstance(items, list) or not items:
                raise ValueError("empty Sina ETF page")
            return [
                {"symbol": str(item["code"]), "name": str(item["name"]), "market": "CN", "asset_type": "etf"}
                for item in items
            ]
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            return [item for page in pool.map(fetch_page, range(1, pages + 1)) for item in page]


def _catalog_key(item: dict) -> str:
    if item["market"] != "CN":
        return item["market"]
    if infer_asset_type(item["name"], item.get("asset_type")) == "etf":
        return "ETF"
    from src.platform.marketdata.cn_symbol import get_cn_exchange
    return "BJ" if get_cn_exchange(item["symbol"]) == "BJ" else "CN"


def refresh_stock_list() -> list[dict]:
    """Fetch stocks and listed ETFs; retain failed catalogs without retaining delistings."""
    stocks = []
    failed = set()
    catalogs = (
        ("CN", _fetch_from_eastmoney),
        ("HK", _fetch_hk_from_eastmoney),
        ("US", _fetch_us_from_eastmoney),
        ("BJ", _fetch_bj_from_eastmoney),
        ("ETF", _fetch_etf_from_eastmoney),
    )
    for catalog, fetch in catalogs:
        try:
            items = fetch()
            if not items:
                raise ValueError("empty catalog")
        except Exception as exc:
            logger.warning("%s catalog fetch failed: %s", catalog, exc)
            items = []
            if catalog == "CN":
                try:
                    items = _akshare_fallback().result(timeout=15)
                except Exception as fallback_exc:
                    logger.warning("A-share fallback failed: %s", fallback_exc)
            elif catalog == "ETF":
                try:
                    items = _fetch_etf_from_sina()
                except Exception as fallback_exc:
                    logger.warning("ETF fallback failed: %s", fallback_exc)
            if not items:
                failed.add(catalog)
        stocks.extend(items)

    previous = _load_cache(allow_stale=True) or []
    stocks.extend(item for item in previous if _catalog_key(item) in failed)
    by_key = {}
    for item in stocks:
        key = (item["market"], item["symbol"])
        by_key.setdefault(key, {**item, "asset_type": infer_asset_type(item["name"], item.get("asset_type"))})
    stocks = list(by_key.values())
    if stocks:
        _save_cache(stocks, ttl=300 if failed else CACHE_TTL)
    return stocks


def get_stock_list() -> list[dict]:
    """获取股票列表(优先缓存)"""
    cached = _load_cache()
    if cached:
        return cached
    return refresh_stock_list() or _load_cache(allow_stale=True) or []


def _normalize_symbol(code: str, market: str) -> str:
    code = (code or "").strip().upper()
    if market in ("CN", "HK"):
        import re
        code = re.sub(r"^(SH|SZ|BJ|HK)(?=\d)", "", code)
        code = re.sub(r"\.(SH|SZ|BJ|HK)$", "", code)
        if market == "HK":
            code = code.zfill(5)
    # USO, SHY and dotted share-class tickers are actual US symbols.
    return code


def _realtime_search(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """东方财富实时搜索 API"""
    import urllib.parse
    # 提高 count 以覆盖更多候选项（包含北交所）
    url = f"https://searchapi.eastmoney.com/api/suggest/get?input={urllib.parse.quote(query)}&type=14&count={limit * 5}"

    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(url, headers=HEADERS)
            data = resp.json()
    except Exception as e:
        logger.warning(f"实时搜索失败: {e}")
        return []

    items = data.get("QuotationCodeTable", {}).get("Data", [])
    if not items:
        return []

    results = []
    seen = set()
    for item in items:
        classify = (item.get("Classify") or "").strip()
        security_type = (item.get("SecurityTypeName") or "").strip()
        code_raw = (item.get("Code") or "").strip().upper()
        name = item.get("Name") or ""
        asset_type = infer_asset_type(name)

        if classify in ("OTCFUND", "Bond", "Index"):
            continue

        # Only exchange-listed ETFs are admitted from the Fund category.
        # OTCFUND, LOFs, indices and bonds remain outside the watchlist search.
        if classify == "Fund":
            if str(item.get("MktNum")) not in ("0", "1") or asset_type != "etf":
                continue
            stock_market = "CN"

        # 判断市场
        elif (
            classify in ("AStock", "BJStock")
            or any(ch in security_type for ch in ("沪", "深", "北"))
            or code_raw.endswith(".BJ")
            or code_raw.startswith("BJ")
        ):
            stock_market = "CN"
        elif classify in ("HK", "HKStock") or "港" in security_type:
            stock_market = "HK"
        elif classify == "UsStock" or "美" in security_type:
            stock_market = "US"
        else:
            continue  # 跳过其他类型（债券、基金等）

        # 市场筛选
        if market and stock_market != market:
            continue

        # 只保留股票（排除债券等）
        type_us = str(item.get("TypeUS") or "")
        if stock_market == "US":
            if type_us and type_us not in ("1", "2", "3", "5"):
                continue
            if type_us == "5":
                asset_type = "etf"

        code = item.get("Code", "")
        symbol = _normalize_symbol(code, stock_market)
        key = (stock_market, symbol)
        if not symbol or key in seen:
            continue
        seen.add(key)

        results.append({
            "symbol": symbol,
            "name": item.get("Name", ""),
            "market": stock_market,
            "asset_type": asset_type,
        })

        if len(results) >= limit:
            break

    return results


def search_stocks(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """搜索股票 - 优先使用实时搜索，失败则使用缓存"""
    q = query.strip()
    if not q:
        return []

    from marketdata.vendors.canada import search_canadian

    def canadian_results(selected_market: str) -> list[dict]:
        return [
            {**item, "asset_type": "etf" if item.get("security_type") == "ETF" else "stock"}
            for item in search_canadian(q, selected_market, limit)
        ]

    if market in {"TSX", "TSXV"}:
        return canadian_results(market)
    results = _realtime_search(q, market, limit)
    if not market:
        try:
            results = canadian_results("") + results
        except Exception as exc:
            logger.warning("Canadian search unavailable; preserving other markets: %s", exc)
    if len(results) >= limit:
        return results[:limit]

    # 实时搜索结果不足时，用缓存补全（便于聚合多市场搜索结果）
    cached = _cached_search(q, market, limit, refresh_if_missing=not results)
    if not results:
        if cached:
            logger.info("实时搜索无结果，使用缓存搜索")
        return cached

    seen = {(r.get("market"), r.get("symbol")) for r in results}
    for r in cached:
        key = (r.get("market"), r.get("symbol"))
        if key in seen:
            continue
        results.append(r)
        seen.add(key)
        if len(results) >= limit:
            break
    return results


def _cached_search(query: str, market: str = "", limit: int = 20, *, refresh_if_missing: bool = True) -> list[dict]:
    """从缓存中模糊搜索股票"""
    # A successful live lookup must not wait for a cold multi-market catalog.
    stocks = get_stock_list() if refresh_if_missing else _load_cache(allow_stale=True) or []
    if not stocks:
        return []

    q = query.strip().upper()
    if not q:
        return []

    results = []
    for s in stocks:
        if market and s["market"] != market:
            continue
        code = s["symbol"].upper()
        name = s["name"].upper()
        # 代码前缀匹配优先
        if code.startswith(q):
            results.append((0, s))
        elif q in name:
            results.append((1, s))
        elif q in code:
            results.append((2, s))

        if len(results) >= limit * 2:
            break

    results.sort(key=lambda x: x[0])
    return [{**r[1], "asset_type": infer_asset_type(r[1]["name"], r[1].get("asset_type"))} for r in results[:limit]]
