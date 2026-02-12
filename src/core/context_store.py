from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import and_

from src.web.database import SessionLocal
from src.web.models import (
    AgentContextRun,
    AgentPredictionOutcome,
    NewsTopicSnapshot,
    StockContextSnapshot,
)
from src.core.json_safe import to_jsonable

logger = logging.getLogger(__name__)


def save_stock_context_snapshot(
    *,
    symbol: str,
    market: str,
    snapshot_date: str,
    context_type: str,
    payload: dict,
    quality: dict | None = None,
) -> bool:
    db = SessionLocal()
    try:
        payload_safe = to_jsonable(payload or {})
        quality_safe = to_jsonable(quality or {})
        existing = (
            db.query(StockContextSnapshot)
            .filter(
                StockContextSnapshot.symbol == symbol,
                StockContextSnapshot.market == market,
                StockContextSnapshot.snapshot_date == snapshot_date,
                StockContextSnapshot.context_type == context_type,
            )
            .first()
        )
        if existing:
            existing.payload = payload_safe
            existing.quality = quality_safe
        else:
            db.add(
                StockContextSnapshot(
                    symbol=symbol,
                    market=market,
                    snapshot_date=snapshot_date,
                    context_type=context_type,
                    payload=payload_safe,
                    quality=quality_safe,
                )
            )
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"保存 stock context snapshot 失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_recent_stock_context_snapshots(
    *,
    symbol: str,
    market: str,
    context_type: str | None = None,
    days: int = 30,
    limit: int = 30,
) -> list[StockContextSnapshot]:
    db = SessionLocal()
    try:
        cutoff = (date.today() - timedelta(days=max(days, 1))).strftime("%Y-%m-%d")
        q = db.query(StockContextSnapshot).filter(
            StockContextSnapshot.symbol == symbol,
            StockContextSnapshot.market == market,
            StockContextSnapshot.snapshot_date >= cutoff,
        )
        if context_type:
            q = q.filter(StockContextSnapshot.context_type == context_type)
        return (
            q.order_by(StockContextSnapshot.snapshot_date.desc())
            .limit(max(1, limit))
            .all()
        )
    finally:
        db.close()


def save_news_topic_snapshot(
    *,
    snapshot_date: str,
    window_days: int,
    symbols: list[str],
    summary: str,
    topics: list[str],
    sentiment: str,
    coverage: dict | None = None,
) -> bool:
    db = SessionLocal()
    try:
        existing = (
            db.query(NewsTopicSnapshot)
            .filter(
                NewsTopicSnapshot.snapshot_date == snapshot_date,
                NewsTopicSnapshot.window_days == window_days,
            )
            .first()
        )
        payload = to_jsonable(
            {
                "symbols": symbols or [],
                "summary": summary or "",
                "topics": topics or [],
                "sentiment": sentiment or "neutral",
                "coverage": coverage or {},
            }
        )
        if existing:
            existing.symbols = payload["symbols"]
            existing.summary = payload["summary"]
            existing.topics = payload["topics"]
            existing.sentiment = payload["sentiment"]
            existing.coverage = payload["coverage"]
        else:
            db.add(
                NewsTopicSnapshot(
                    snapshot_date=snapshot_date,
                    window_days=int(window_days),
                    symbols=payload["symbols"],
                    summary=payload["summary"],
                    topics=payload["topics"],
                    sentiment=payload["sentiment"],
                    coverage=payload["coverage"],
                )
            )
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"保存 news topic snapshot 失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_latest_news_topic_snapshot(
    *,
    window_days: int = 7,
) -> NewsTopicSnapshot | None:
    db = SessionLocal()
    try:
        return (
            db.query(NewsTopicSnapshot)
            .filter(NewsTopicSnapshot.window_days == int(window_days))
            .order_by(NewsTopicSnapshot.snapshot_date.desc())
            .first()
        )
    finally:
        db.close()


def save_agent_context_run(
    *,
    agent_name: str,
    stock_symbol: str,
    analysis_date: str,
    context_payload: dict,
    quality: dict | None = None,
) -> bool:
    db = SessionLocal()
    try:
        payload_safe = to_jsonable(context_payload or {})
        quality_safe = to_jsonable(quality or {})
        db.add(
            AgentContextRun(
                agent_name=agent_name,
                stock_symbol=stock_symbol or "*",
                analysis_date=analysis_date,
                context_payload=payload_safe,
                quality=quality_safe,
            )
        )
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"保存 agent context run 失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def list_recent_agent_context_runs(
    *,
    agent_name: str,
    stock_symbol: str | None = None,
    days: int = 30,
    limit: int = 50,
) -> list[AgentContextRun]:
    db = SessionLocal()
    try:
        cutoff = (date.today() - timedelta(days=max(days, 1))).strftime("%Y-%m-%d")
        q = db.query(AgentContextRun).filter(
            AgentContextRun.agent_name == agent_name,
            AgentContextRun.analysis_date >= cutoff,
        )
        if stock_symbol:
            q = q.filter(AgentContextRun.stock_symbol == stock_symbol)
        return q.order_by(AgentContextRun.created_at.desc()).limit(max(1, limit)).all()
    finally:
        db.close()


def save_agent_prediction_outcome(
    *,
    agent_name: str,
    stock_symbol: str,
    stock_market: str,
    prediction_date: str,
    horizon_days: int,
    action: str,
    action_label: str,
    confidence: float | None = None,
    trigger_price: float | None = None,
    meta: dict | None = None,
) -> bool:
    db = SessionLocal()
    try:
        meta_safe = to_jsonable(meta or {})
        db.add(
            AgentPredictionOutcome(
                agent_name=agent_name,
                stock_symbol=stock_symbol,
                stock_market=stock_market,
                prediction_date=prediction_date,
                horizon_days=max(1, int(horizon_days)),
                action=action or "watch",
                action_label=action_label or "观望",
                confidence=confidence,
                trigger_price=trigger_price,
                outcome_status="pending",
                meta=meta_safe,
            )
        )
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"保存 prediction outcome 失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def mark_agent_prediction_outcome(
    *,
    record_id: int,
    outcome_price: float | None,
    outcome_return_pct: float | None,
    status: str = "evaluated",
) -> bool:
    db = SessionLocal()
    try:
        rec = (
            db.query(AgentPredictionOutcome)
            .filter(AgentPredictionOutcome.id == int(record_id))
            .first()
        )
        if not rec:
            return False
        rec.outcome_price = outcome_price
        rec.outcome_return_pct = outcome_return_pct
        rec.outcome_status = status
        rec.evaluated_at = datetime.now()
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"更新 prediction outcome 失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def list_pending_prediction_outcomes(
    *,
    max_horizon_days: int = 10,
    limit: int = 300,
) -> list[AgentPredictionOutcome]:
    db = SessionLocal()
    try:
        today = date.today().strftime("%Y-%m-%d")
        q = db.query(AgentPredictionOutcome).filter(
            and_(
                AgentPredictionOutcome.outcome_status == "pending",
                AgentPredictionOutcome.horizon_days <= max_horizon_days,
                AgentPredictionOutcome.prediction_date <= today,
            )
        )
        return (
            q.order_by(
                AgentPredictionOutcome.prediction_date.asc(),
                AgentPredictionOutcome.created_at.asc(),
            )
            .limit(limit)
            .all()
        )
    finally:
        db.close()


def list_agent_prediction_outcomes(
    *,
    agent_name: str | None = None,
    stock_symbol: str | None = None,
    status: str | None = None,
    days: int = 90,
    limit: int = 200,
) -> list[AgentPredictionOutcome]:
    db = SessionLocal()
    try:
        cutoff = (date.today() - timedelta(days=max(days, 1))).strftime("%Y-%m-%d")
        q = db.query(AgentPredictionOutcome).filter(
            AgentPredictionOutcome.prediction_date >= cutoff
        )
        if agent_name:
            q = q.filter(AgentPredictionOutcome.agent_name == agent_name)
        if stock_symbol:
            q = q.filter(AgentPredictionOutcome.stock_symbol == stock_symbol)
        if status:
            q = q.filter(AgentPredictionOutcome.outcome_status == status)
        return (
            q.order_by(
                AgentPredictionOutcome.prediction_date.desc(),
                AgentPredictionOutcome.created_at.desc(),
            )
            .limit(max(1, limit))
            .all()
        )
    finally:
        db.close()


def cleanup_context_data(
    *,
    snapshot_days: int = 180,
    topic_days: int = 180,
    context_run_days: int = 180,
    outcome_days: int = 365,
) -> dict:
    db = SessionLocal()
    deleted = {
        "stock_context_snapshots": 0,
        "news_topic_snapshots": 0,
        "agent_context_runs": 0,
        "agent_prediction_outcomes": 0,
    }
    try:
        snapshot_cutoff = (
            date.today() - timedelta(days=max(1, int(snapshot_days)))
        ).strftime("%Y-%m-%d")
        topic_cutoff = (
            date.today() - timedelta(days=max(1, int(topic_days)))
        ).strftime("%Y-%m-%d")
        context_run_cutoff = (
            date.today() - timedelta(days=max(1, int(context_run_days)))
        ).strftime("%Y-%m-%d")
        outcome_cutoff = (
            date.today() - timedelta(days=max(1, int(outcome_days)))
        ).strftime("%Y-%m-%d")

        deleted["stock_context_snapshots"] = (
            db.query(StockContextSnapshot)
            .filter(StockContextSnapshot.snapshot_date < snapshot_cutoff)
            .delete(synchronize_session=False)
        )
        deleted["news_topic_snapshots"] = (
            db.query(NewsTopicSnapshot)
            .filter(NewsTopicSnapshot.snapshot_date < topic_cutoff)
            .delete(synchronize_session=False)
        )
        deleted["agent_context_runs"] = (
            db.query(AgentContextRun)
            .filter(AgentContextRun.analysis_date < context_run_cutoff)
            .delete(synchronize_session=False)
        )
        deleted["agent_prediction_outcomes"] = (
            db.query(AgentPredictionOutcome)
            .filter(AgentPredictionOutcome.prediction_date < outcome_cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        return deleted
    except Exception as e:
        logger.warning(f"清理 context 数据失败: {e}")
        db.rollback()
        return deleted
    finally:
        db.close()
