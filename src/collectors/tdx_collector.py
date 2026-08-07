"""通达信问小达投研数据采集器(盘前/盘后 Agent 用)。

封装 TDX MCP 的自然语言问答能力(实际工具 tdx_screener),为 PanWatch 的
盘前/盘后 Agent 提供通达信独家数据:
  - 个股行情 / 智能选股(主力净流入、涨幅、估值等多条件)
  - 板块排行 / 题材资金流向
  - 财务 / 技术 / 资金流向

调用方(agent collect 块)应 try/except 包裹,TDX 失败不影响主流程(优雅降级)。
底层直连 TDX MCP endpoint, 鉴权走环境变量 TDX_API_KEY(容器注入, 不进代码)。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 盘前关注的投研问题(自然语言, 直接喂 tdx_screener)
PREMARKET_QUERIES = [
    "今日主力净流入前10的A股",
    "今日涨幅前10的概念板块",
    "近3日主力净流入前10的半导体",
]

# 盘后关注的投研问题
POSTMARKET_QUERIES = [
    "今日涨停家数最多的概念板块",
    "今日主力净流入前10的A股",
    "今日龙虎榜机构净买入前10",
]


def _ask(question: str, *, config: dict | None = None) -> dict | None:
    """单条问小达查询, 返回 {meta, headers, data} 或 None。"""
    try:
        from marketdata.vendors.tdx import ask_wenda

        return ask_wenda(question, config=config)
    except Exception as e:
        logger.warning(f"TDX 问小达查询失败 q={question!r}: {e}")
        return None


def collect_wenda(queries: list[str], *, config: dict | None = None) -> dict:
    """批量问小达查询, 返回 {question: result} 字典。

    单条失败仅该条为 None, 不影响其他条。
    """
    out: dict[str, dict | None] = {}
    for q in queries:
        out[q] = _ask(q, config=config)
    ok = sum(1 for v in out.values() if v)
    logger.info(f"TDX 问小达采集: {ok}/{len(queries)} 成功")
    return out


class TdxWendaCollector:
    """盘前/盘后 Agent 用的问小达采集器(与 WudaoMCPClient 同层)。"""

    def __init__(self, *, config: dict | None = None):
        self.config = config

    def collect_premarket(self) -> dict:
        return collect_wenda(PREMARKET_QUERIES, config=self.config)

    def collect_postmarket(self) -> dict:
        return collect_wenda(POSTMARKET_QUERIES, config=self.config)
