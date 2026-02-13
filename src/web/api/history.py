"""分析历史 API"""

import logging
from datetime import timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import AnalysisHistory
from src.config import Settings
from src.core.agent_catalog import (
    AGENT_KIND_CAPABILITY,
    AGENT_KIND_WORKFLOW,
    CAPABILITY_AGENT_NAMES,
    infer_agent_kind,
)


def _format_datetime(dt) -> str:
    """格式化时间为当前时区的 ISO 格式。"""
    if not dt:
        return ""

    tz_name = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc

    # Deterministic rule:
    # - naive datetime: treat as UTC
    # - aware datetime: keep original timezone semantics
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(tzinfo).isoformat(timespec="seconds")


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/history", tags=["history"])


class HistoryResponse(BaseModel):
    id: int
    agent_name: str
    agent_kind: str = AGENT_KIND_WORKFLOW
    stock_symbol: str
    analysis_date: str
    title: str
    content: str
    suggestions: dict | None = (
        None  # 个股建议 {symbol: {action, action_label, reason, should_alert}}
    )
    news: list[dict] | None = None
    quality_overview: dict | None = None
    context_summary: dict | None = None
    context_payload: dict | None = None
    prompt_context: str | None = None
    prompt_stats: dict | None = None
    news_debug: dict | None = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


@router.get("")
def list_history(
    agent_name: str | None = None,
    stock_symbol: str | None = None,
    kind: str = Query(default=AGENT_KIND_WORKFLOW),
    limit: int = Query(default=30, le=100),
    db: Session = Depends(get_db),
) -> list[HistoryResponse]:
    """获取分析历史列表"""
    query = db.query(AnalysisHistory)

    if agent_name:
        query = query.filter(AnalysisHistory.agent_name == agent_name)
    if stock_symbol:
        query = query.filter(AnalysisHistory.stock_symbol == stock_symbol)
    kind_norm = (kind or "").strip().lower()
    if kind_norm == AGENT_KIND_CAPABILITY:
        query = query.filter(
            or_(
                AnalysisHistory.agent_kind_snapshot == AGENT_KIND_CAPABILITY,
                and_(
                    or_(
                        AnalysisHistory.agent_kind_snapshot.is_(None),
                        AnalysisHistory.agent_kind_snapshot == "",
                    ),
                    AnalysisHistory.agent_name.in_(CAPABILITY_AGENT_NAMES),
                ),
            )
        )
    elif kind_norm == AGENT_KIND_WORKFLOW:
        query = query.filter(
            or_(
                AnalysisHistory.agent_kind_snapshot == AGENT_KIND_WORKFLOW,
                and_(
                    or_(
                        AnalysisHistory.agent_kind_snapshot.is_(None),
                        AnalysisHistory.agent_kind_snapshot == "",
                    ),
                    ~AnalysisHistory.agent_name.in_(CAPABILITY_AGENT_NAMES),
                ),
            )
        )

    records = (
        query.order_by(
            AnalysisHistory.analysis_date.desc(),
            AnalysisHistory.updated_at.desc(),
            AnalysisHistory.id.desc(),
        )
        .limit(limit)
        .all()
    )

    return [
        HistoryResponse(
            id=r.id,
            agent_name=r.agent_name,
            agent_kind=(r.agent_kind_snapshot or infer_agent_kind(r.agent_name)),
            stock_symbol=r.stock_symbol,
            analysis_date=r.analysis_date,
            title=r.title or "",
            content=r.content,
            suggestions=r.raw_data.get("suggestions") if r.raw_data else None,
            news=r.raw_data.get("news") if r.raw_data else None,
            quality_overview=r.raw_data.get("quality_overview") if r.raw_data else None,
            context_summary=r.raw_data.get("context_summary") if r.raw_data else None,
            context_payload=r.raw_data.get("context_payload") if r.raw_data else None,
            prompt_context=r.raw_data.get("prompt_context") if r.raw_data else None,
            prompt_stats=r.raw_data.get("prompt_stats") if r.raw_data else None,
            news_debug=r.raw_data.get("news_debug") if r.raw_data else None,
            created_at=_format_datetime(r.created_at),
            updated_at=_format_datetime(r.updated_at),
        )
        for r in records
    ]


@router.get("/{history_id}")
def get_history_detail(
    history_id: int, db: Session = Depends(get_db)
) -> HistoryResponse:
    """获取单条分析详情"""
    record = db.query(AnalysisHistory).filter(AnalysisHistory.id == history_id).first()
    if not record:
        from fastapi import HTTPException

        raise HTTPException(404, "记录不存在")

    return HistoryResponse(
        id=record.id,
        agent_name=record.agent_name,
        agent_kind=(record.agent_kind_snapshot or infer_agent_kind(record.agent_name)),
        stock_symbol=record.stock_symbol,
        analysis_date=record.analysis_date,
        title=record.title or "",
        content=record.content,
        suggestions=record.raw_data.get("suggestions") if record.raw_data else None,
        news=record.raw_data.get("news") if record.raw_data else None,
        quality_overview=record.raw_data.get("quality_overview")
        if record.raw_data
        else None,
        context_summary=record.raw_data.get("context_summary")
        if record.raw_data
        else None,
        context_payload=record.raw_data.get("context_payload")
        if record.raw_data
        else None,
        prompt_context=record.raw_data.get("prompt_context")
        if record.raw_data
        else None,
        prompt_stats=record.raw_data.get("prompt_stats")
        if record.raw_data
        else None,
        news_debug=record.raw_data.get("news_debug")
        if record.raw_data
        else None,
        created_at=_format_datetime(record.created_at),
        updated_at=_format_datetime(record.updated_at),
    )


@router.delete("/{history_id}")
def delete_history(history_id: int, db: Session = Depends(get_db)):
    """删除单条历史记录"""
    record = db.query(AnalysisHistory).filter(AnalysisHistory.id == history_id).first()
    if not record:
        from fastapi import HTTPException

        raise HTTPException(404, "记录不存在")

    db.delete(record)
    db.commit()
    return {"ok": True}
