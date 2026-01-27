"""日志中心 API"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.web.database import get_db
from src.web.models import LogEntry


def _format_datetime(dt) -> str:
    """格式化时间为带时区的 ISO 格式"""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


router = APIRouter()


class LogEntryResponse(BaseModel):
    id: int
    timestamp: str
    level: str
    logger_name: str
    message: str

    class Config:
        from_attributes = True


class LogListResponse(BaseModel):
    items: list[LogEntryResponse]
    total: int


@router.get("", response_model=LogListResponse)
def list_logs(
    level: str = Query("", description="日志级别过滤，逗号分隔"),
    q: str = Query("", description="关键词搜索"),
    logger: str = Query("", description="Logger 名称过滤"),
    since: str = Query("", description="起始时间 ISO 格式"),
    until: str = Query("", description="结束时间 ISO 格式"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(LogEntry)

    if level:
        levels = [l.strip().upper() for l in level.split(",") if l.strip()]
        if levels:
            query = query.filter(LogEntry.level.in_(levels))

    if q:
        query = query.filter(LogEntry.message.contains(q))

    if logger:
        query = query.filter(LogEntry.logger_name.contains(logger))

    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            query = query.filter(LogEntry.timestamp >= since_dt)
        except ValueError:
            pass

    if until:
        try:
            until_dt = datetime.fromisoformat(until)
            query = query.filter(LogEntry.timestamp <= until_dt)
        except ValueError:
            pass

    total = query.count()
    items = (
        query.order_by(LogEntry.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return LogListResponse(
        items=[
            LogEntryResponse(
                id=item.id,
                timestamp=_format_datetime(item.timestamp),
                level=item.level,
                logger_name=item.logger_name or "",
                message=item.message or "",
            )
            for item in items
        ],
        total=total,
    )


@router.delete("")
def clear_logs(db: Session = Depends(get_db)):
    count = db.query(LogEntry).delete()
    db.commit()
    return {"deleted": count}
