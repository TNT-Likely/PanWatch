"""日志中心 API"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.web.database import get_db
from src.web.models import LogEntry
from src.web.log_handler import get_log_handler_stats


def _format_datetime(dt) -> str:
    """格式化时间为带时区的 ISO 格式"""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


router = APIRouter()

INFRA_LOGGER_PREFIXES = (
    "httpx",
    "httpcore",
    "urllib3",
    "uvicorn.access",
    "sqlalchemy.engine",
)


def _infra_logger_expr():
    return or_(*[LogEntry.logger_name.startswith(p) for p in INFRA_LOGGER_PREFIXES])


def _parse_iso(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class LogEntryResponse(BaseModel):
    id: int
    timestamp: str
    level: str
    logger_name: str
    message: str
    trace_id: str = ""
    run_id: str = ""
    agent_name: str = ""
    event: str = ""
    tags: dict | None = None
    notify_status: str = ""
    notify_reason: str = ""

    class Config:
        from_attributes = True


class LogListResponse(BaseModel):
    items: list[LogEntryResponse]
    total: int
    has_more: bool = False
    next_before_id: int | None = None


@router.get("", response_model=LogListResponse)
def list_logs(
    level: str = Query("", description="日志级别过滤，逗号分隔"),
    q: str = Query("", description="关键词搜索"),
    logger: str = Query("", description="Logger 名称过滤"),
    trace_id: str = Query("", description="链路追踪ID"),
    run_id: str = Query("", description="运行ID"),
    agent_name: str = Query("", description="Agent 名称过滤"),
    event: str = Query("", description="事件过滤"),
    notify_status: str = Query("", description="通知状态过滤: attempted/skipped/sent/failed"),
    domain: str = Query("all", description="日志域: all/business/infra"),
    since: str = Query("", description="起始时间 ISO 格式"),
    until: str = Query("", description="结束时间 ISO 格式"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    before_id: int = Query(0, ge=0, description="cursor 分页: 取该 id 之前的日志"),
    db: Session = Depends(get_db),
):
    query = db.query(LogEntry)

    if level:
        levels = [l.strip().upper() for l in level.split(",") if l.strip()]
        if levels:
            query = query.filter(LogEntry.level.in_(levels))

    if q:
        query = query.filter(
            or_(
                LogEntry.message.contains(q),
                LogEntry.logger_name.contains(q),
                LogEntry.trace_id.contains(q),
                LogEntry.agent_name.contains(q),
                LogEntry.event.contains(q),
            )
        )

    if logger:
        parts = [p.strip() for p in logger.split(",") if p.strip()]
        if len(parts) == 1:
            query = query.filter(LogEntry.logger_name.contains(parts[0]))
        elif parts:
            query = query.filter(or_(*[LogEntry.logger_name.contains(p) for p in parts]))

    if trace_id:
        query = query.filter(LogEntry.trace_id == trace_id)
    if run_id:
        query = query.filter(LogEntry.run_id == run_id)
    if agent_name:
        query = query.filter(LogEntry.agent_name == agent_name)
    if event:
        parts = [p.strip() for p in event.split(",") if p.strip()]
        if len(parts) == 1:
            query = query.filter(LogEntry.event == parts[0])
        elif parts:
            query = query.filter(LogEntry.event.in_(parts))
    if notify_status:
        query = query.filter(LogEntry.notify_status == notify_status)

    domain_norm = (domain or "all").strip().lower()
    infra_expr = _infra_logger_expr()
    if domain_norm == "business":
        query = query.filter(~infra_expr)
    elif domain_norm == "infra":
        query = query.filter(infra_expr)

    if since:
        try:
            since_dt = _parse_iso(since)
            if since_dt is None:
                raise ValueError("invalid since")
            query = query.filter(LogEntry.timestamp >= since_dt)
        except ValueError:
            pass

    if until:
        try:
            until_dt = _parse_iso(until)
            if until_dt is None:
                raise ValueError("invalid until")
            query = query.filter(LogEntry.timestamp <= until_dt)
        except ValueError:
            pass

    total = query.count()
    has_more = False
    next_before_id = None

    if before_id > 0:
        rows = (
            query.filter(LogEntry.id < before_id)
            .order_by(LogEntry.id.desc())
            .limit(limit + 1)
            .all()
        )
        if len(rows) > limit:
            has_more = True
            rows = rows[:limit]
        if rows:
            next_before_id = rows[-1].id
        items = rows
    else:
        items = (
            query.order_by(LogEntry.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        has_more = (offset + len(items)) < total
        if items:
            next_before_id = items[-1].id

    return LogListResponse(
        items=[
            LogEntryResponse(
                id=item.id,
                timestamp=_format_datetime(item.timestamp),
                level=item.level,
                logger_name=item.logger_name or "",
                message=item.message or "",
                trace_id=item.trace_id or "",
                run_id=item.run_id or "",
                agent_name=item.agent_name or "",
                event=item.event or "",
                tags=item.tags or {},
                notify_status=item.notify_status or "",
                notify_reason=item.notify_reason or "",
            )
            for item in items
        ],
        total=total,
        has_more=has_more,
        next_before_id=next_before_id,
    )


@router.delete("")
def clear_logs(db: Session = Depends(get_db)):
    count = db.query(LogEntry).delete()
    db.commit()
    return {"deleted": count}


@router.get("/meta")
def logs_meta(
    domain: str = Query("all", description="日志域: all/business/infra"),
    since: str = Query("", description="起始时间 ISO 格式"),
    db: Session = Depends(get_db),
):
    query = db.query(LogEntry)

    domain_norm = (domain or "all").strip().lower()
    infra_expr = _infra_logger_expr()
    if domain_norm == "business":
        query = query.filter(~infra_expr)
    elif domain_norm == "infra":
        query = query.filter(infra_expr)

    if since:
        try:
            since_dt = _parse_iso(since)
            if since_dt is None:
                raise ValueError("invalid since")
            query = query.filter(LogEntry.timestamp >= since_dt)
        except ValueError:
            pass

    total = query.count()
    level_dist = (
        query.with_entities(LogEntry.level, func.count(LogEntry.id))
        .group_by(LogEntry.level)
        .all()
    )
    logger_dist = (
        query.with_entities(LogEntry.logger_name, func.count(LogEntry.id))
        .group_by(LogEntry.logger_name)
        .order_by(func.count(LogEntry.id).desc())
        .limit(30)
        .all()
    )
    event_dist = (
        query.with_entities(LogEntry.event, func.count(LogEntry.id))
        .filter(LogEntry.event != "")
        .group_by(LogEntry.event)
        .order_by(func.count(LogEntry.id).desc())
        .limit(20)
        .all()
    )

    return {
        "total": total,
        "levels": {k: int(v) for k, v in level_dist if k},
        "top_loggers": [
            {"logger_name": k or "", "count": int(v)} for k, v in logger_dist if k
        ],
        "top_events": [{"event": k or "", "count": int(v)} for k, v in event_dist if k],
    }


@router.get("/health")
def logs_health(db: Session = Depends(get_db)):
    total = db.query(LogEntry).count()
    infra_expr = _infra_logger_expr()
    infra_count = db.query(LogEntry).filter(infra_expr).count()
    business_count = max(total - infra_count, 0)
    oldest = db.query(LogEntry).order_by(LogEntry.id.asc()).first()
    newest = db.query(LogEntry).order_by(LogEntry.id.desc()).first()
    return {
        "storage": {
            "total": total,
            "business_count": business_count,
            "infra_count": infra_count,
            "oldest": _format_datetime(oldest.timestamp) if oldest else "",
            "newest": _format_datetime(newest.timestamp) if newest else "",
        },
        "writer": get_log_handler_stats(),
    }
