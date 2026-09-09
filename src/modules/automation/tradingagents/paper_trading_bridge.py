"""TradingAgents 决策 → 模拟盘 StrategySignalRun 信号。

PaperTradingEngine._check_entries() 周期性扫 StrategySignalRun(status=active, action=buy/add)
并自动开模拟仓位。本模块把 TA 的 BUY/SELL 决策写入这张表,让模拟盘消费。

默认 enable_paper_trading=False 防止误开仓。用户在 agent_config.config 启用。
"""

from __future__ import annotations

import logging
from datetime import date

from src.modules.automation.tradingagents.result_mapper import DECISION_LABEL_MAP

logger = logging.getLogger(__name__)


def maybe_emit_paper_trading_signal(
    *,
    stock_symbol: str,
    stock_market: str,
    stock_name: str,
    decision: str,
    confidence: float,
    signal_text: str,
    reason: str,
    current_price: float | None,
    enabled: bool,
) -> bool:
    """将 TA 决策写入 StrategySignalRun。返回是否实际写入。

    - 仅 enabled=True 且 decision in (buy, add) 时写入(SELL 不开新仓)
    - entry_low/high 用当前价 ±2% 作为入场区间
    - stop_loss 用入场价 -5%,target_price +10%(粗粒度,可以 Phase C 让 TA 输出更精确)
    - 同标的同日去重:strategy_code+source_candidate_id 唯一性
    """
    if not enabled:
        return False
    action = (decision or "").lower()
    if action not in ("buy", "add"):
        return False
    if not current_price or current_price <= 0:
        logger.warning(
            f"[TA paper] {stock_symbol} 当前价缺失,跳过写信号"
        )
        return False

    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import StrategySignalRun

    snapshot_date = date.today().isoformat()
    entry_low = round(current_price * 0.98, 2)
    entry_high = round(current_price * 1.02, 2)
    stop_loss = round(current_price * 0.95, 3)
    target_price = round(current_price * 1.10, 3)

    db = SessionLocal()
    try:
        # 同标的当日重复触发 → upsert(source_candidate_id 是 Integer,用 0 当 TA 专用 sentinel)
        source_id = 0
        existing = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.snapshot_date == snapshot_date,
                StrategySignalRun.stock_symbol == stock_symbol,
                StrategySignalRun.stock_market == stock_market,
                StrategySignalRun.strategy_code == "tradingagents",
                StrategySignalRun.source_candidate_id == source_id,
            )
            .first()
        )
        if existing:
            existing.action = action
            existing.action_label = DECISION_LABEL_MAP.get(action, "买入")
            existing.signal = signal_text[:500]
            existing.reason = reason[:1000]
            existing.confidence = confidence
            existing.entry_low = entry_low
            existing.entry_high = entry_high
            existing.stop_loss = stop_loss
            existing.target_price = target_price
            existing.status = "active"
        else:
            row = StrategySignalRun(
                snapshot_date=snapshot_date,
                stock_symbol=stock_symbol,
                stock_market=stock_market,
                stock_name=stock_name or stock_symbol,
                strategy_code="tradingagents",
                strategy_name="TradingAgents 深度分析",
                strategy_version="v1",
                risk_level="medium",
                source_pool="watchlist",
                score=float(confidence or 5.0),
                rank_score=float(confidence or 5.0) * 10,  # 给个偏向中等的分数
                confidence=float(confidence or 5.0) / 10,
                status="active",
                action=action,
                action_label=DECISION_LABEL_MAP.get(action, "买入"),
                signal=signal_text[:500],
                reason=reason[:1000],
                evidence=[],
                holding_days=10,  # TA 给的 time_horizon 是中长期
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=stop_loss,
                target_price=target_price,
                invalidation="价格跌破止损位 / 基本面恶化",
                plan_quality=70,
                source_agent="tradingagents",
                source_candidate_id=source_id,
            )
            db.add(row)
        db.commit()
        logger.info(
            f"[TA paper] 已写信号: {stock_symbol} {action} "
            f"entry=[{entry_low}, {entry_high}] stop={stop_loss} target={target_price}"
        )
        return True
    except Exception as e:
        logger.warning(f"[TA paper] 写 StrategySignalRun 失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()
