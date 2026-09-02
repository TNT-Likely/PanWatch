"""验证中心 API：面向产品展示的 Agent 建议复盘。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core.agent_prediction_evaluation import (
    EVALUATION_POLICY,
    group_prediction_outcomes,
    summarize_prediction_groups,
)
from src.core.prediction_outcome import evaluate_pending_prediction_outcomes
from src.web.database import get_db
from src.web.models import AgentPredictionOutcome


router = APIRouter()


def query_prediction_rows(
    *,
    db: Session,
    agent_name: str | None = None,
    market: str | None = None,
    action: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    horizon_unit: str | None = "trading_days",
    days: int = 90,
) -> list[AgentPredictionOutcome]:
    """加载一组原始 horizon 记录，聚合与分页交给调用方完成。"""
    cutoff = (date.today() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    query = db.query(AgentPredictionOutcome).filter(
        AgentPredictionOutcome.prediction_date >= cutoff
    )
    if agent_name:
        query = query.filter(AgentPredictionOutcome.agent_name == agent_name)
    if market:
        query = query.filter(AgentPredictionOutcome.stock_market == market.upper())
    if action:
        query = query.filter(AgentPredictionOutcome.action == action.lower())
    if start_date:
        query = query.filter(AgentPredictionOutcome.prediction_date >= start_date)
    if end_date:
        query = query.filter(AgentPredictionOutcome.prediction_date <= end_date)
    if horizon_unit and horizon_unit != "all":
        query = query.filter(AgentPredictionOutcome.horizon_unit == horizon_unit)
    return query.order_by(
        AgentPredictionOutcome.prediction_date.desc(),
        AgentPredictionOutcome.created_at.desc(),
    ).all()


def filter_prediction_groups_by_status(
    groups: list[dict], status: str | None
) -> list[dict]:
    """按建议组筛选状态，始终保留其全部 horizon 结果。"""
    if not status:
        return groups
    return [
        group
        for group in groups
        if any(
            outcome.get("status") == status
            for outcome in (group.get("outcomes") or {}).values()
        )
    ]


def _available_filters(rows: list[AgentPredictionOutcome]) -> dict[str, list[str]]:
    return {
        "agent_names": sorted({row.agent_name for row in rows if row.agent_name}),
        "markets": sorted({row.stock_market for row in rows if row.stock_market}),
        "actions": sorted({row.action for row in rows if row.action}),
        "statuses": sorted({row.outcome_status for row in rows if row.outcome_status}),
        "horizon_units": sorted({row.horizon_unit for row in rows if row.horizon_unit}),
    }


@router.get("/agent-predictions")
def list_agent_predictions(
    agent_name: str | None = None,
    market: str | None = None,
    action: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    horizon_unit: Annotated[str | None, Query()] = "trading_days",
    days: int = Query(default=90, ge=1, le=720),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0, le=3000),
    db: Session = Depends(get_db),
):
    """按建议组返回复盘行；默认只展示交易日口径。"""
    rows = query_prediction_rows(
        db=db,
        agent_name=agent_name,
        market=market,
        action=action,
        status=status,
        start_date=start_date,
        end_date=end_date,
        horizon_unit=horizon_unit,
        days=days,
    )
    groups = filter_prediction_groups_by_status(
        group_prediction_outcomes(rows), status
    )
    return {
        "items": groups[offset : offset + limit],
        "total": len(groups),
        "available_filters": _available_filters(rows),
        "policy": EVALUATION_POLICY,
    }


@router.get("/agent-predictions/summary")
def get_agent_prediction_summary(
    agent_name: str | None = None,
    market: str | None = None,
    action: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    horizon_unit: Annotated[str | None, Query()] = "trading_days",
    days: int = Query(default=90, ge=1, le=720),
    db: Session = Depends(get_db),
):
    """返回与列表筛选一致的命中、覆盖与样本量汇总。"""
    rows = query_prediction_rows(
        db=db,
        agent_name=agent_name,
        market=market,
        action=action,
        status=status,
        start_date=start_date,
        end_date=end_date,
        horizon_unit=horizon_unit,
        days=days,
    )
    groups = filter_prediction_groups_by_status(
        group_prediction_outcomes(rows), status
    )
    return summarize_prediction_groups(groups)


@router.post("/agent-predictions/evaluate")
def evaluate_agent_predictions(
    max_horizon_days: int = Query(default=5, ge=1, le=5),
    limit: int = Query(default=300, ge=1, le=300),
):
    """手动检查已到期建议；未到期记录保持 pending。"""
    return evaluate_pending_prediction_outcomes(
        max_horizon_days=max_horizon_days,
        limit=limit,
    )
