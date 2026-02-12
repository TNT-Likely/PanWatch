"""上下文与后验评估 API。"""

from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.config import Settings
from src.core.context_store import (
    cleanup_context_data,
    get_latest_news_topic_snapshot,
    get_recent_stock_context_snapshots,
    list_agent_prediction_outcomes,
    list_recent_agent_context_runs,
)
from src.core.prediction_outcome import evaluate_pending_prediction_outcomes

router = APIRouter(prefix="/context", tags=["context"])


def _format_datetime(dt) -> str:
    if not dt:
        return ""
    tz_name = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo).isoformat()


class StockContextSnapshotResponse(BaseModel):
    id: int
    symbol: str
    market: str
    snapshot_date: str
    context_type: str
    quality: dict
    payload: dict
    created_at: str


class AgentContextRunResponse(BaseModel):
    id: int
    agent_name: str
    stock_symbol: str
    analysis_date: str
    quality: dict
    context_payload: dict
    created_at: str


class AgentPredictionOutcomeResponse(BaseModel):
    id: int
    agent_name: str
    stock_symbol: str
    stock_market: str
    prediction_date: str
    horizon_days: int
    action: str
    action_label: str
    confidence: float | None = None
    trigger_price: float | None = None
    outcome_price: float | None = None
    outcome_return_pct: float | None = None
    outcome_status: str
    meta: dict
    evaluated_at: str
    created_at: str


@router.get("/snapshots/{symbol}", response_model=list[StockContextSnapshotResponse])
def list_stock_context_snapshots(
    symbol: str,
    market: str = "CN",
    context_type: str | None = None,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=30, ge=1, le=200),
):
    rows = get_recent_stock_context_snapshots(
        symbol=symbol,
        market=market,
        context_type=context_type,
        days=days,
        limit=limit,
    )
    return [
        StockContextSnapshotResponse(
            id=r.id,
            symbol=r.symbol,
            market=r.market,
            snapshot_date=r.snapshot_date,
            context_type=r.context_type,
            quality=r.quality or {},
            payload=r.payload or {},
            created_at=_format_datetime(r.created_at),
        )
        for r in rows
    ]


@router.get("/topics/latest")
def get_latest_topic(window_days: int = Query(default=7, ge=1, le=90)):
    row = get_latest_news_topic_snapshot(window_days=window_days)
    if not row:
        return {"exists": False}
    return {
        "exists": True,
        "id": row.id,
        "snapshot_date": row.snapshot_date,
        "window_days": row.window_days,
        "summary": row.summary or "",
        "topics": row.topics or [],
        "sentiment": row.sentiment or "neutral",
        "symbols": row.symbols or [],
        "coverage": row.coverage or {},
        "created_at": _format_datetime(row.created_at),
    }


@router.get("/runs", response_model=list[AgentContextRunResponse])
def list_context_runs(
    agent_name: str,
    stock_symbol: str | None = None,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=300),
):
    rows = list_recent_agent_context_runs(
        agent_name=agent_name,
        stock_symbol=stock_symbol,
        days=days,
        limit=limit,
    )
    return [
        AgentContextRunResponse(
            id=r.id,
            agent_name=r.agent_name,
            stock_symbol=r.stock_symbol,
            analysis_date=r.analysis_date,
            quality=r.quality or {},
            context_payload=r.context_payload or {},
            created_at=_format_datetime(r.created_at),
        )
        for r in rows
    ]


@router.get("/predictions", response_model=list[AgentPredictionOutcomeResponse])
def list_prediction_outcomes(
    agent_name: str | None = None,
    stock_symbol: str | None = None,
    status: str | None = None,
    days: int = Query(default=90, ge=1, le=720),
    limit: int = Query(default=200, ge=1, le=1000),
):
    rows = list_agent_prediction_outcomes(
        agent_name=agent_name,
        stock_symbol=stock_symbol,
        status=status,
        days=days,
        limit=limit,
    )
    return [
        AgentPredictionOutcomeResponse(
            id=r.id,
            agent_name=r.agent_name,
            stock_symbol=r.stock_symbol,
            stock_market=r.stock_market,
            prediction_date=r.prediction_date,
            horizon_days=r.horizon_days,
            action=r.action,
            action_label=r.action_label,
            confidence=r.confidence,
            trigger_price=r.trigger_price,
            outcome_price=r.outcome_price,
            outcome_return_pct=r.outcome_return_pct,
            outcome_status=r.outcome_status,
            meta=r.meta or {},
            evaluated_at=_format_datetime(r.evaluated_at),
            created_at=_format_datetime(r.created_at),
        )
        for r in rows
    ]


@router.post("/predictions/evaluate")
def evaluate_predictions(
    max_horizon_days: int = Query(default=10, ge=1, le=30),
    limit: int = Query(default=300, ge=1, le=2000),
):
    return evaluate_pending_prediction_outcomes(
        max_horizon_days=max_horizon_days,
        limit=limit,
    )


@router.post("/cleanup")
def cleanup_context(
    snapshot_days: int = Query(default=180, ge=30, le=2000),
    topic_days: int = Query(default=180, ge=30, le=2000),
    context_run_days: int = Query(default=180, ge=30, le=2000),
    outcome_days: int = Query(default=365, ge=60, le=4000),
):
    return cleanup_context_data(
        snapshot_days=snapshot_days,
        topic_days=topic_days,
        context_run_days=context_run_days,
        outcome_days=outcome_days,
    )
