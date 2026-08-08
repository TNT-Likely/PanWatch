"""8010 接入 PanWatch 市场数据源(龙虎榜) — 经 8000 HTTP 代理端点。

设计原则(符合"所有 key 在设置→接口Key 维护"的要求):
- 不直接连 marketdata 包/读 DB 副本(那样 key 不实时)
- 改走 8000 的 /api/market-data/dragon-tiger/{date} 端点
  该端点内部用 marketdata ftshare vendor, config=None → 自动从容器 DB 的
  data_sources 表读「设置→接口Key」配置的 key(改了立即生效, 无需重启)
- 这样 8010 永远拿到 UI 维护的最新 key, 国内外服务器只要 UI 配好源即可

认证: 8000 端点带 protected 依赖(需 Bearer token), 用 admin 账号登录拿 token。
"""
from __future__ import annotations

import logging
import sys
import time

logger = logging.getLogger(__name__)

# 8000 主后端地址(容器内/本机)
PANWATCH_BASE = "http://127.0.0.1:8000"
# 缓存 token(登录一次, 5 分钟有效)
_TOKEN: str | None = None
_TOKEN_TS: float = 0.0
_TOKEN_TTL = 300.0

# 进程内缓存(龙虎榜日频, 避免重复请求)
_DT_CACHE: dict = {}


def _get_token() -> str | None:
    """用 admin 账号登录 8000 拿 Bearer token(缓存 5 分钟)。"""
    global _TOKEN, _TOKEN_TS
    now = time.time()
    if _TOKEN and now - _TOKEN_TS < _TOKEN_TTL:
        return _TOKEN
    try:
        import urllib.request, json
        req = urllib.request.Request(
            f"{PANWATCH_BASE}/api/auth/login",
            data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        _TOKEN = data.get("data", {}).get("token")
        _TOKEN_TS = now
        return _TOKEN
    except Exception as e:
        logger.warning(f"8000 登录失败: {e}")
        return None


def _http_get(path: str) -> dict | None:
    import urllib.request, json
    token = _get_token()
    if not token:
        return None
    req = urllib.request.Request(
        f"{PANWATCH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get_dragon_tiger(date: str | None = None, symbol: str | None = None) -> list:
    """获取龙虎榜(经 8000 /api/market-data/dragon-tiger, key 来自 UI 配置)。

    date: YYYYMMDD, 不传则用最近一个交易日(周五回退)
    返回 list of dict:
      [{trade_date, symbol, name, close, change_pct, net_buy, buy_amt, sell_amt, on_list(bool)}]
    """
    if date is None:
        date = _latest_trade_date()
    if date in _DT_CACHE:
        items = _DT_CACHE[date]
    else:
        try:
            resp = _http_get(f"/api/market-data/dragon-tiger/{date}")
            raw = resp.get("data", resp) if resp else {}
            items = raw.get("items", []) if isinstance(raw, dict) else []
            _DT_CACHE[date] = items
            logger.info(f"龙虎榜({date})经8000获取 {len(items)} 条")
        except Exception as e:
            logger.warning(f"龙虎榜经8000获取失败 [{date}]: {e}")
            return []

    if symbol:
        sym_norm = symbol.replace(".SZ", "").replace(".SH", "")
        hit = [r for r in items if r.get("symbol", "").replace(".SZ", "").replace(".SH", "") == sym_norm]
        return hit
    return items


def _latest_trade_date() -> str:
    """最近交易日(周末回退到周五, 简化版)。"""
    from datetime import datetime, timedelta
    d = datetime.now()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")
