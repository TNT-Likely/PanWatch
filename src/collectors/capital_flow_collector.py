"""资金流向采集器 - 经 marketdata 包统一接入"""
import logging

from dataclasses import dataclass

from src.collectors.market_http import TTLCache
from src.models.market import MarketCode

logger = logging.getLogger(__name__)

# 资金流为日级数据、变动慢:中等 TTL 缓存,避免每轮重复拉。
_FLOW_CACHE = TTLCache(default_ttl_sec=600.0)


@dataclass
class CapitalFlow:
    """资金流向数据"""
    symbol: str
    name: str

    # 今日资金流（单位：元）
    main_net_inflow: float      # 主力净流入
    main_net_inflow_pct: float  # 主力净流入占比
    super_net_inflow: float     # 超大单净流入
    big_net_inflow: float       # 大单净流入
    mid_net_inflow: float       # 中单净流入
    small_net_inflow: float     # 小单净流入

    # 5日资金流
    main_net_5d: float | None = None  # 5日主力净流入


def get_market_data():
    """惰性导入,避免模块加载时的循环依赖(便于测试 monkeypatch)。"""
    from src.core.marketdata_client import get_market_data as _g
    return _g()


class CapitalFlowCollector:
    """资金流向采集器"""

    def __init__(self, market: MarketCode):
        self.market = market

    def get_capital_flow(self, symbol: str) -> CapitalFlow | None:
        """获取单只股票的资金流向。

        实时主力净额优先用悟道 intraday_main_flow(盘中快照), 四档(超大/大/中/小)用
        Engine(腾讯/东财实时四档)补全。智兔资金流是盘后 T+1 批量, 不作实时源。
        """
        cache_key = f"{self.market.value}:{symbol}"
        cached = _FLOW_CACHE.get(cache_key)
        if cached is not None:
            return cached

        capital_flow = None
        # 1) 悟道盘中实时主力净额(优先)
        try:
            from src.collectors.wudao_mcp_client import WudaoMCPClient
            wc = WudaoMCPClient()
            wc._initialize()
            r = wc.call_tool("intraday_main_flow", {"codes": [symbol]})
            if isinstance(r, dict) and "text" in r:
                import re
                m = re.search(r"主力净额\s*([-\d.]+)\s*万", r["text"])
                if m:
                    main_net = float(m.group(1)) * 1e4
                    capital_flow = CapitalFlow(
                        symbol=symbol, name="",
                        main_net_inflow=main_net,
                        main_net_inflow_pct=0.0,
                        super_net_inflow=0.0, big_net_inflow=0.0,
                        mid_net_inflow=0.0, small_net_inflow=0.0,
                        main_net_5d=None,
                    )
        except Exception as e:
            logger.debug(f"悟道资金流失败, 回退 Engine: {e}")

        # 2) Engine 四档实时(腾讯/东财)补全
        md_cf = get_market_data().capital_flow(symbol, market=self.market.value)
        if md_cf is not None:
            if capital_flow is None:
                capital_flow = CapitalFlow(
                    symbol=md_cf.symbol, name=md_cf.name,
                    main_net_inflow=md_cf.main_net_inflow,
                    main_net_inflow_pct=md_cf.main_net_inflow_pct,
                    super_net_inflow=md_cf.super_net_inflow,
                    big_net_inflow=md_cf.big_net_inflow,
                    mid_net_inflow=md_cf.mid_net_inflow,
                    small_net_inflow=md_cf.small_net_inflow,
                    main_net_5d=md_cf.main_net_5d,
                )
            else:
                # 悟道实时净额优先, 四档用 Engine
                capital_flow.super_net_inflow = md_cf.super_net_inflow
                capital_flow.big_net_inflow = md_cf.big_net_inflow
                capital_flow.mid_net_inflow = md_cf.mid_net_inflow
                capital_flow.small_net_inflow = md_cf.small_net_inflow
                capital_flow.main_net_5d = md_cf.main_net_5d
                if md_cf.name:
                    capital_flow.name = md_cf.name

        if capital_flow is None:
            return None
        _FLOW_CACHE.set(cache_key, capital_flow)
        return capital_flow

    def get_capital_flow_summary(self, symbol: str) -> dict:
        """获取资金流向摘要（用于 prompt）"""
        flow = self.get_capital_flow(symbol)

        if not flow:
            return {"error": "无资金流向数据"}

        # 判断资金状态
        if flow.main_net_inflow > 0:
            if flow.main_net_inflow_pct > 10:
                status = "主力大幅流入"
            elif flow.main_net_inflow_pct > 5:
                status = "主力明显流入"
            else:
                status = "主力小幅流入"
        elif flow.main_net_inflow < 0:
            if flow.main_net_inflow_pct < -10:
                status = "主力大幅流出"
            elif flow.main_net_inflow_pct < -5:
                status = "主力明显流出"
            else:
                status = "主力小幅流出"
        else:
            status = "主力资金平衡"

        # 5日趋势
        trend_5d = "无数据"
        if flow.main_net_5d is not None:
            if flow.main_net_5d > 0:
                trend_5d = f"5日净流入{flow.main_net_5d/1e8:.2f}亿"
            else:
                trend_5d = f"5日净流出{abs(flow.main_net_5d)/1e8:.2f}亿"

        return {
            "status": status,
            "main_net_inflow": flow.main_net_inflow,
            "main_net_inflow_pct": flow.main_net_inflow_pct,
            "super_net_inflow": flow.super_net_inflow,
            "big_net_inflow": flow.big_net_inflow,
            "mid_net_inflow": flow.mid_net_inflow,
            "small_net_inflow": flow.small_net_inflow,
            "trend_5d": trend_5d,
        }
