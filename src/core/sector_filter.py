"""题材/板块筛选支持:ftsahre 板块工具封装 + 缓存。

数据流: 前端选题材名 → 本模块按名称匹配 ftshare 概念板块(code/name)
→ 取板块成分股(6 位代码) → 调用方用 SQL IN 过滤。

ftshare 接口(云服务器直连稳,免 key):
- ft_eastmoney_concept_boards: 全部概念板块列表(~486 条), {code: BK0963, name: 商业航天}
- ft_eastmoney_board_constituents: {board_code: BK0963} → {constituents: [{stock_code, stock_name}]}

缓存: 板块列表 1h / 成分股按板块 1h(模块级 dict,线程安全)。
"""

from __future__ import annotations

import logging
import threading
import time

from marketdata.vendors.ftshare import _get_client

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_boards_cache: tuple[float, list[dict]] | None = None  # (ts, boards)
_constituents_cache: dict[str, tuple[float, list[str]]] = {}  # board_code -> (ts, [codes])

_BOARDS_TTL_S = 3600.0
_CONSTITUENTS_TTL_S = 3600.0


def list_concept_boards() -> list[dict]:
    """全部概念板块列表 [{code, name}]。失败返回 []。"""
    global _boards_cache
    now = time.monotonic()
    with _cache_lock:
        if _boards_cache and now - _boards_cache[0] < _BOARDS_TTL_S:
            return _boards_cache[1]
    try:
        client = _get_client({})
        rows = client.call_tool("ft_eastmoney_concept_boards", {}) or []
        boards = []
        for r in rows:
            if isinstance(r, dict) and r.get("code") and r.get("name"):
                boards.append({"code": str(r["code"]), "name": str(r["name"])})
        if boards:
            with _cache_lock:
                _boards_cache = (now, boards)
            return boards
    except Exception as e:
        logger.warning(f"概念板块列表获取失败: {e}")
    return []


def search_boards(query: str, limit: int = 20) -> list[dict]:
    """按名称模糊搜索板块(子串匹配,不区分大小写)。空 query 返回前 limit 个。"""
    q = (query or "").strip().lower()
    boards = list_concept_boards()
    if q:
        hits = [b for b in boards if q in b["name"].lower()]
    else:
        hits = boards
    return hits[: max(1, min(limit, 50))]


def board_constituents(board_code: str) -> list[str]:
    """板块成分股 6 位代码列表。失败返回 []。"""
    code = (board_code or "").strip()
    if not code:
        return []
    now = time.monotonic()
    with _cache_lock:
        cached = _constituents_cache.get(code)
        if cached and now - cached[0] < _CONSTITUENTS_TTL_S:
            return cached[1]
    try:
        client = _get_client({})
        rows = client.call_tool("ft_eastmoney_board_constituents", {"board_code": code}) or []
        codes: list[str] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            for c in (r.get("constituents") or []):
                if isinstance(c, dict) and c.get("stock_code"):
                    sc = str(c["stock_code"]).strip()
                    if sc.isdigit() and len(sc) == 6:
                        codes.append(sc)
        if codes:
            with _cache_lock:
                _constituents_cache[code] = (now, codes)
            return codes
    except Exception as e:
        logger.warning(f"板块成分股获取失败 {code}: {e}")
    return []


def resolve_sector_codes(sector_name: str) -> list[str]:
    """题材名 → 成分股代码列表。用于 SQL IN 过滤。

    匹配策略: ① 板块名精确匹配 → ② 名称包含匹配(取第一个) → ③ 失败返回 []。
    """
    name = (sector_name or "").strip()
    if not name:
        return []
    boards = list_concept_boards()
    target = None
    for b in boards:
        if b["name"] == name:
            target = b
            break
    if target is None:
        for b in boards:
            if name in b["name"]:
                target = b
                break
    if target is None:
        logger.info(f"题材 [{name}] 未匹配到概念板块")
        return []
    codes = board_constituents(target["code"])
    if not codes:
        logger.info(f"题材 [{name}] 板块 {target['code']} 无成分股")
    return codes
