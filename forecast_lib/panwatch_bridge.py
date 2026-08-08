"""从 PanWatch 主后端(8000, 容器内 marketdata/tdx 数据源)获取准确的东财口径资金流。

为什么不用 8010 直连:
- 8010 宿主机没有 marketdata 包(在 8000 容器内)
- zhitu MCP 的"主买-主卖"口径和东财主力净流入口径差 3 倍以上(已核实)

方案:
- 复用 8000 已有的 tdx ask API(走容器内 marketdata/tdx, 东财口径, 准确可核对)
- 不改 8000 数据源逻辑, 只 HTTP 读取已有接口

依赖: 8000 健康且可访问(http://127.0.0.1:8000)
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

_PANWATCH_BASE = "http://127.0.0.1:8000"
_TOKEN_CACHE: dict = {}


def _get_token() -> str:
    """登录 8000 拿 admin token(缓存)。"""
    if _TOKEN_CACHE.get("token"):
        return _TOKEN_CACHE["token"]
    try:
        req = urllib.request.Request(
            f"{_PANWATCH_BASE}/api/auth/login",
            data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        tok = data.get("data", {}).get("token", "")
        if tok:
            _TOKEN_CACHE["token"] = tok
        return tok
    except Exception as e:
        logger.warning(f"获取 8000 token 失败: {e}")
        return ""


def _tdx_ask(query: str) -> Optional[dict]:
    """调 8000 tdx ask, 返回结构化 rows/headers 或 None。"""
    tok = _get_token()
    if not tok:
        return None
    try:
        url = f"{_PANWATCH_BASE}/api/tdx/ask?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
        return data.get("data")
    except Exception as e:
        logger.warning(f"tdx ask 失败 [{query}]: {e}")
        return None


def _parse_main_net(d) -> Optional[float]:
    """从 tdx ask 返回里解析'主力净额'字段(单位元)。"""
    if not d:
        return None
    rows = d.get("rows") or []
    if not rows:
        return None
    headers = d.get("headers") or []
    # 找含'主力净额'的列
    col = None
    for h in headers:
        if "主力净额" in str(h):
            col = h
            break
    if not col:
        return None
    val = rows[0].get(col)
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def fetch_capital_flow(symbol: str, days: int = 5) -> list:
    """获取东财口径主力资金流(经 PanWatch 8000)。

    返回 list of dict(单元素或聚合), 结构:
      [{
        "date": "近5日" / 当日日期,
        "main_net": float,        # 主力净流入(元)
        "source": "panwatch-tdx",
        "note": "东财口径(超大单+大单净买入)"
      }]
    """
    out = []
    # 当日主力净流入
    d_today = _tdx_ask(f"{symbol} 主力净流入")
    today_net = _parse_main_net(d_today)
    if today_net is not None:
        # 尝试从 header 拿日期(格式如 "主力净额<br>2026.08.070#")
        date_label = "当日"
        headers = (d_today or {}).get("headers") or []
        for h in headers:
            if "主力净额" in str(h) and "20" in str(h):
                # 提取 2026.08.07 形式
                import re
                m = re.search(r"20\d{2}\.\d{2}\.\d{2}", str(h))
                if m:
                    date_label = m.group(0)
                break
        out.append({
            "date": date_label,
            "main_net": today_net,
            "source": "panwatch-tdx",
            "note": "东财口径(超大单+大单净买入)",
        })
    # 近 N 日合计(趋势)
    d_5 = _tdx_ask(f"{symbol} 近五日主力净流入")
    net_5 = _parse_main_net(d_5)
    if net_5 is not None:
        out.append({
            "date": f"近{days}日",
            "main_net": net_5,
            "source": "panwatch-tdx",
            "note": "东财口径(近5日合计)",
        })
    return out


import urllib.parse  # noqa: E402  (放末尾避免循环 import 顺序问题)
