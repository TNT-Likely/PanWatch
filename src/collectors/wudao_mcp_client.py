"""wudao MCP 竞价数据采集器(HTTP 直连,免 Hermes)。

通过 MCP JSON-RPC 协议调用 wudao 竞价工具(auction_opening_snapshot /
auction_theme_strength / auction_market_scan 等),为 PanWatch 竞价复盘
Agent 提供 wudao 独家数据:consistency(题材一致性)/ bidStrength(竞价强度)/
limitBuyAmountAfter920(9:20后留存委买) 等。

配置:环境变量 WUDAO_MCP_URL + WUDAO_MCP_TOKEN,或 PanWatch 设置页数据源。
"""
from __future__ import annotations

import json
import logging

import requests

logger = logging.getLogger(__name__)

_DEFAULT_URL = "https://stock.quicktiny.cn/api/mcp"
_PROTOCOL_VERSION = "2024-11-05"


class WudaoMCPClient:
    """wudao MCP 客户端:initialize + tools/call。

    配置优先级:显式参数 > 环境变量 WUDAO_MCP_URL / WUDAO_MCP_TOKEN。
    """

    def __init__(self, url: str | None = None, token: str | None = None):
        import os

        self.url = url or os.getenv("WUDAO_MCP_URL") or _DEFAULT_URL
        self.token = token or os.getenv("WUDAO_MCP_TOKEN") or ""
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"
        self._initialized = False

    def _initialize(self):
        if self._initialized:
            return
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "panwatch-wudao", "version": "1.0"},
            },
        }
        r = requests.post(self.url, headers=self._headers, json=payload, timeout=30)
        r.raise_for_status()
        # notifications/initialized
        requests.post(
            self.url,
            headers=self._headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=10,
        )
        self._initialized = True

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """调用 wudao 工具,返回解析后的 data 对象。"""
        self._initialize()
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        r = requests.post(self.url, headers=self._headers, json=payload, timeout=60)
        r.raise_for_status()
        result = r.json()
        content = (result.get("result") or {}).get("content") or []
        if not content:
            return {}
        text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
        # 尝试解析 JSON(部分工具直接返回 JSON 文本)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("success"):
                return parsed.get("data", {}) or {}
            return parsed
        except json.JSONDecodeError:
            return {"text": text}

    def auction_opening_snapshot(self, limit: int = 30) -> dict:
        """竞价全景:竞价涨停/跌停/昨炸板反馈/委买额/成交额。"""
        return self.call_tool("auction_opening_snapshot", {"limit": limit})

    def auction_theme_strength(self, limit: int = 10, theme_source: str = "concept") -> dict:
        """题材竞价强度:consistency(一致性)/totalBidAmount/leaders。"""
        return self.call_tool(
            "auction_theme_strength",
            {"themeSource": theme_source, "limit": limit},
        )

    def auction_market_scan(
        self,
        sort_by: str = "bidStrength",
        limit: int = 10,
        min_bid_amount: int | None = None,
    ) -> dict:
        """竞价个股扫描:bidStrength/consistency/弱转强。"""
        args = {"sortBy": sort_by, "limit": limit}
        if min_bid_amount:
            args["minBidAmount"] = min_bid_amount
        return self.call_tool("auction_market_scan", args)

    def auction_weak_to_strong(self, limit: int = 20) -> dict:
        """弱转强候选(昨炸板/昨涨停分歧今日反包)。"""
        return self.call_tool("auction_weak_to_strong", {"limit": limit})

    def auction_limitup_feedback(self, focus: str = "all", group_by: str = "streak") -> dict:
        """昨涨停今竞价反馈:高标被核/接力/情绪信号。"""
        return self.call_tool(
            "auction_limitup_feedback",
            {"focus": focus, "groupBy": group_by},
        )
