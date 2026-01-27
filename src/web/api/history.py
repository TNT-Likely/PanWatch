"""分析历史 API"""
import logging
from datetime import date, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import AnalysisHistory


def _format_datetime(dt) -> str:
    """格式化时间为带时区的 ISO 格式"""
    if not dt:
        return ""
    # SQLite 存储的时间没有时区，假设为 UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/history", tags=["history"])


class HistoryResponse(BaseModel):
    id: int
    agent_name: str
    stock_symbol: str
    analysis_date: str
    title: str
    content: str
    suggestions: dict | None = None  # 个股建议 {symbol: {action, action_label, reason, should_alert}}
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


@router.get("")
def list_history(
    agent_name: str | None = None,
    stock_symbol: str | None = None,
    limit: int = Query(default=30, le=100),
    db: Session = Depends(get_db),
) -> list[HistoryResponse]:
    """获取分析历史列表"""
    query = db.query(AnalysisHistory)

    if agent_name:
        query = query.filter(AnalysisHistory.agent_name == agent_name)
    if stock_symbol:
        query = query.filter(AnalysisHistory.stock_symbol == stock_symbol)

    records = query.order_by(AnalysisHistory.analysis_date.desc()).limit(limit).all()

    return [
        HistoryResponse(
            id=r.id,
            agent_name=r.agent_name,
            stock_symbol=r.stock_symbol,
            analysis_date=r.analysis_date,
            title=r.title or "",
            content=r.content,
            suggestions=r.raw_data.get("suggestions") if r.raw_data else None,
            created_at=_format_datetime(r.created_at),
            updated_at=_format_datetime(r.updated_at),
        )
        for r in records
    ]


@router.get("/{history_id}")
def get_history_detail(history_id: int, db: Session = Depends(get_db)) -> HistoryResponse:
    """获取单条分析详情"""
    record = db.query(AnalysisHistory).filter(AnalysisHistory.id == history_id).first()
    if not record:
        from fastapi import HTTPException
        raise HTTPException(404, "记录不存在")

    return HistoryResponse(
        id=record.id,
        agent_name=record.agent_name,
        stock_symbol=record.stock_symbol,
        analysis_date=record.analysis_date,
        title=record.title or "",
        content=record.content,
        suggestions=record.raw_data.get("suggestions") if record.raw_data else None,
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
