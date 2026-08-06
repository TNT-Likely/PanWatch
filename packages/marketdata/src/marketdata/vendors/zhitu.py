"""智兔数服 vendor:分红送配(东财分红的免费备源)。

智兔(zhituapi.com)是纯 HTTP GET + token 参数,比 ftshare 的 MCP 协议更简单,
PanWatch 容器内可直接 urllib 请求,无需 MCP 客户端。

实测(2026-08-07,云服务器容器内直连 200):
- 分红送配历史: GET https://api.zhituapi.com/hs/gs/jnff/{code}?token=...
  返回数组,字段:
    sdate  方案日期(YYYY-MM-DD)
    send   每10股派息(元) —— 茅台 280.242 / 神剑 0.5
    give   每10股送股(股)
    change 每10股转增(股)
    line   方案进度(实施/不分配/预案)
    cdate  除权除息日(实施时才有;不分配为 "--")
    edate  股权登记日
- 注意: 200 次/天额度,仅作东财断供时的备源,别做默认主源。
- 股东户数 zhitu 无对应接口(/gs/sdgd 十大股东 ≠ 户数),不接入。

错误处理:任何失败返回 [],不阻断降级链。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from marketdata.types import DividendItem
from marketdata.vendors.base import DividendVendor

logger = logging.getLogger(__name__)

_ZHITU_BASE = "https://api.zhituapi.com/hs"
# token 优先级: env > server.py 默认值。PanWatch 容器用 -e ZHITU_TOKEN 注入。
_ZHITU_TOKEN = os.environ.get("ZHITU_TOKEN", "E0E16C43-9272-4DAB-800C-178694F2D4B1")


def _to_float(value) -> float | None:
    if value is None or value == "" or value == "-" or value == "--":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _zhitu_get(path: str, params: dict | None = None, timeout: float = 15.0) -> list | None:
    """GET 智兔接口,返回 list;失败/结构异常返回 None。"""
    q = {"token": _ZHITU_TOKEN, **(params or {})}
    url = f"{_ZHITU_BASE}{path}?{urllib.parse.urlencode(q)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except Exception as e:
        logger.warning(f"智兔分红请求失败 {path}: {type(e).__name__}: {e}")
        return None


class ZhituDividendVendor(DividendVendor):
    """分红送配备源:按 symbol 逐只请求,返回全部分红历史(东财断供时兜底)。

    字段映射: ex_date=cdate(除权除息日), dividend_per_share=send/10(每股派息),
    transfer_ratio=change(每10股转增), bonus_ratio=give(每10股送股), progress=line。
    """

    name = "zhitu"
    supports_markets = {"CN"}

    def fetch(self, symbols: list, config: dict) -> list[DividendItem]:
        if not symbols:
            return []
        out: list[DividendItem] = []
        for sym in symbols:
            rows = _zhitu_get(f"/gs/jnff/{sym.code}")
            if not rows:
                continue
            for row in rows:
                try:
                    ex_date = str(row.get("cdate") or "")
                    # 未实施(不分配/预案)时 cdate 为 "--",保留但标记进度
                    if ex_date in ("", "--", "None"):
                        ex_date = ""
                    out.append(
                        DividendItem(
                            ex_date=ex_date,
                            symbol=sym.code,
                            # send 单位是"每10股派息(元)"→ 每股派息 = send/10
                            dividend_per_share=(_to_float(row.get("send")) or 0.0) / 10.0,
                            transfer_ratio=_to_float(row.get("change")),  # 每10股转增
                            bonus_ratio=_to_float(row.get("give")),       # 每10股送股
                            progress=str(row.get("line") or ""),
                        )
                    )
                except Exception as e:
                    logger.debug(f"智兔分红行解析失败 symbol={sym.code}: {e}")
                    continue
        return out
