"""盘中急涨/急跌联动:自动触发 TradingAgents 深度分析。

设计:
- intraday_monitor 完成单只股票分析后,调用 `try_auto_trigger`
- 触发条件(MVP):|change_pct| >= threshold(默认 5%,从 tradingagents 配置读)
- 护栏:冷却时间(默认 24h)+ 月度预算(复用 cost_tracker)
- 默认关闭(enabled=false),需在 Agents 列表「深度配置」里显式打开

为什么不直接复用 BaseAgent.run:
- intraday_monitor 是单次循环里跑很多股票,每只都可能触发,需要 fire-and-forget
- 触发后的 TA 分析走 trigger_agent_for_stock 自身的异步队列,避免阻塞主循环
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from src.web.database import SessionLocal
from src.web.models import AgentConfig, AnalysisHistory

logger = logging.getLogger(__name__)

DEFAULT_CHANGE_PCT_THRESHOLD = 5.0
DEFAULT_COOLDOWN_HOURS = 24


def _read_auto_trigger_config(db: Session) -> dict | None:
    """从 AgentConfig.config 读 auto_trigger 配置。

    Returns:
        {
            "enabled": bool,
            "change_pct_threshold": float,
            "cooldown_hours": int,
        } 或 None(未配置/未启用)
    """
    agent = db.query(AgentConfig).filter(AgentConfig.name == "tradingagents").first()
    if not agent:
        return None
    raw = agent.config or {}
    auto = raw.get("auto_trigger") or {}
    if not auto.get("enabled"):
        return None
    return {
        "enabled": True,
        "change_pct_threshold": float(auto.get("change_pct_threshold") or DEFAULT_CHANGE_PCT_THRESHOLD),
        "cooldown_hours": int(auto.get("cooldown_hours") or DEFAULT_COOLDOWN_HOURS),
    }


def _within_cooldown(db: Session, stock_symbol: str, cooldown_hours: int) -> bool:
    """检查最近 N 小时内是否已为该股触发过 TA 分析(任何来源)。"""
    cutoff = datetime.utcnow() - timedelta(hours=cooldown_hours)
    recent = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == "tradingagents",
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.created_at >= cutoff,
        )
        .first()
    )
    return recent is not None


def _budget_allows(db: Session) -> bool:
    """检查月度预算是否还有余量。预算从 tradingagents 的 config.monthly_budget_usd 读。"""
    try:
        from src.agents.tradingagents.cost_tracker import check_budget
    except ImportError:
        return True

    agent = db.query(AgentConfig).filter(AgentConfig.name == "tradingagents").first()
    if not agent:
        return True
    raw = agent.config or {}
    budget = float(raw.get("monthly_budget_usd") or 0.0)
    if budget <= 0:
        return True  # 没设上限 = 不限制

    try:
        status = check_budget(budget)
        return not status.get("exceeded", False)
    except Exception as e:
        logger.warning(f"[auto_trigger] 预算检查失败,放行: {e}")
        return True


def should_auto_trigger(
    stock_symbol: str,
    change_pct: float | None,
) -> tuple[bool, str]:
    """判断是否应该触发 TA 深度分析。

    Returns:
        (should_trigger, reason)
    """
    if change_pct is None:
        return False, "无涨跌幅数据"

    db = SessionLocal()
    try:
        cfg = _read_auto_trigger_config(db)
        if not cfg:
            return False, "auto_trigger 未启用"

        if abs(change_pct) < cfg["change_pct_threshold"]:
            return False, f"涨跌幅 {change_pct:+.2f}% 未达阈值 {cfg['change_pct_threshold']}%"

        if _within_cooldown(db, stock_symbol, cfg["cooldown_hours"]):
            return False, f"冷却中(最近 {cfg['cooldown_hours']}h 已触发过)"

        if not _budget_allows(db):
            return False, "月度预算已用完"

        return True, f"涨跌幅 {change_pct:+.2f}% 达阈值 {cfg['change_pct_threshold']}%"
    finally:
        db.close()


def fire_and_forget_trigger(stock: Any, source_agent: str = "intraday_monitor") -> str | None:
    """异步触发 TA 深度分析,不阻塞调用方。

    Args:
        stock: 至少包含 symbol/name/market 的对象(StockData 或 ORM Stock)
        source_agent: 触发源 agent 名(用于日志/trace_id)

    Returns:
        trace_id 或 None(触发失败)
    """
    import time as _time

    try:
        from server import trigger_agent_for_stock
    except ImportError:
        logger.warning("[auto_trigger] server.trigger_agent_for_stock 不可用,跳过")
        return None

    symbol = getattr(stock, "symbol", None)
    if not symbol:
        return None

    trace_id = f"auto-{source_agent}-{symbol}-{int(_time.time() * 1000)}"

    async def _run():
        try:
            await trigger_agent_for_stock(
                "tradingagents",
                stock,
                stock_agent_id=None,
                bypass_throttle=True,
                bypass_market_hours=True,
                suppress_notify=False,
                trace_id=trace_id,
                force_refresh=False,
            )
            logger.info(f"[auto_trigger] TA 联动触发完成 - {symbol} (trace={trace_id})")
        except Exception:
            logger.exception(f"[auto_trigger] TA 联动触发失败 - {symbol}")

    try:
        # 优先在当前事件循环 schedule;无 loop 则起新线程兜底
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_run())
        else:
            import threading
            t = threading.Thread(target=lambda: asyncio.run(_run()), daemon=True)
            t.start()
    except RuntimeError:
        import threading
        t = threading.Thread(target=lambda: asyncio.run(_run()), daemon=True)
        t.start()

    return trace_id


def try_auto_trigger(stock: Any, source_agent: str = "intraday_monitor") -> str | None:
    """组合调用:判断 + 触发。

    供 intraday_monitor.analyze 完成后调用。返回 trace_id 或 None。
    """
    symbol = getattr(stock, "symbol", "") or ""
    change_pct = getattr(stock, "change_pct", None)

    ok, reason = should_auto_trigger(symbol, change_pct)
    if not ok:
        logger.debug(f"[auto_trigger] 不触发 {symbol}: {reason}")
        return None

    logger.info(f"[auto_trigger] 触发 TA 深度分析 - {symbol} ({reason})")
    return fire_and_forget_trigger(stock, source_agent)
