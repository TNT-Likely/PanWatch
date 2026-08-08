"""市场数据代理 API: 把 marketdata 包的关键方法暴露成只读 HTTP 端点。

设计原则(与 calendar.py 一致):
- 直接调用 marketdata 包的 vendor/Engine, **不重写数据源逻辑**
- config=None → vendor 自动从容器 DB 的 data_sources 表读 UI 维护的 key
  (即「设置 → 接口Key」配置的凭证, 改了立即生效, 无需重启)
- 供 8010 预测引擎在宿主机调用(宿主机无 marketdata 包, 经 8000 HTTP 取数)

暴露:
- GET /api/market-data/dragon-tiger/{date}  龙虎榜(ftshare vendor)
- GET /api/market-data/capital-flow/{symbol}  资金流(经 MarketData Engine, 走 UI 配置 vendor)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/dragon-tiger/{trade_date}")
async def dragon_tiger_proxy(
    trade_date: str,
    market: str = Query("CN", description="市场"),
):
    """龙虎榜(经 marketdata dragon_tiger vendor, 实际用 ftshare)。

    trade_date: YYYYMMDD
    key 来自「设置→接口Key」配置的 data_sources(type=dragon_tiger), 实时生效。
    """
    try:
        from src.core.marketdata_client import get_market_data
        md = get_market_data()
        rows = md.dragon_tiger(date=trade_date, market=market)
        return {
            "trade_date": trade_date,
            "market": market,
            "count": len(rows) if rows else 0,
            "items": [
                {
                    "trade_date": getattr(i, "trade_date", trade_date),
                    "symbol": getattr(i, "symbol", ""),
                    "name": getattr(i, "name", ""),
                    "close": getattr(i, "close", None),
                    "change_pct": getattr(i, "change_pct", None),
                    "net_buy": getattr(i, "net_buy", None),
                    "buy_amt": getattr(i, "buy_amt", None),
                    "sell_amt": getattr(i, "sell_amt", None),
                    "reason": getattr(i, "reason", None),
                }
                for i in (rows or [])
            ],
        }
    except Exception as e:
        logger.warning(f"龙虎榜代理失败 [{trade_date}]: {e}")
        raise HTTPException(502, f"数据源调用失败: {e}")


@router.get("/capital-flow/{symbol}")
async def capital_flow_proxy(
    symbol: str,
    market: str = Query("CN", description="市场"),
):
    """资金流(经 MarketData Engine, 走 UI 配置的 capital_flow vendor, 默认 sina/eastmoney)。

    key 来自「设置→接口Key」配置的 data_sources(type=capital_flow), 实时生效。
    """
    try:
        from src.core.marketdata_client import get_market_data
        md = get_market_data()
        cf = md.capital_flow(symbol, market=market)
        if cf is None:
            return {"symbol": symbol, "market": market, "error": "no_data"}
        return {
            "symbol": symbol,
            "market": market,
            "main_net_inflow": cf.main_net_inflow,
            "main_net_inflow_pct": cf.main_net_inflow_pct,
            "super_net_inflow": cf.super_net_inflow,
            "big_net_inflow": cf.big_net_inflow,
            "mid_net_inflow": cf.mid_net_inflow,
            "small_net_inflow": cf.small_net_inflow,
            "main_net_5d": cf.main_net_5d,
        }
    except Exception as e:
        logger.warning(f"资金流代理失败 [{symbol}]: {e}")
        raise HTTPException(502, f"数据源调用失败: {e}")
