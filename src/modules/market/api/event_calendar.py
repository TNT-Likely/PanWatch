"""事件日历 API:GET 日期窗 / POST 单条与批量 / PUT / DELETE。

业务规则在 event_calendar_service,这里只做 HTTP 编排(仿 price_alerts)。
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.modules.market import event_calendar_service
from src.platform.persistence.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class EventCalendarItemIn(BaseModel):
    event_date: str = Field(..., description="事件日期 YYYY-MM-DD")
    level: str = Field(default="medium", description="high/medium/low")
    name: str = Field(..., description="事件名")
    scope: str = Field(default="", description="影响范围,如 全球/中国/行业:半导体")
    expected: str = Field(default="", description="预期值")
    actual: str = Field(default="", description="实际值")
    direction: str = Field(default="", description="bullish/bearish/neutral")
    impact_boards: list[Any] = Field(default_factory=list, description="受影响板块")
    meta: dict[str, Any] = Field(default_factory=dict, description="扩展信息")


class EventCalendarBatchIn(BaseModel):
    items: list[EventCalendarItemIn] = Field(..., min_length=1)


class EventCalendarUpdate(BaseModel):
    event_date: str | None = None
    level: str | None = None
    name: str | None = None
    scope: str | None = None
    expected: str | None = None
    actual: str | None = None
    direction: str | None = None
    impact_boards: list[Any] | None = None
    meta: dict[str, Any] | None = None


@router.get("")
def list_events(
    start: str | None = None,
    end: str | None = None,
    level: str | None = None,
    db: Session = Depends(get_db),
):
    """按日期窗列出事件;默认未来 30 天。"""
    try:
        rows = event_calendar_service.list_by_window(db, start=start, end=end, level=level)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [event_calendar_service.to_response(r) for r in rows]


@router.post("")
def create_event(body: EventCalendarItemIn, db: Session = Depends(get_db)):
    """新建单条事件(同日同名存在时转为更新,幂等)。"""
    try:
        result = event_calendar_service.upsert_items(db, [body.model_dump()])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"created": result["created"], "updated": result["updated"]}


@router.post("/batch")
def create_events_batch(body: EventCalendarBatchIn, db: Session = Depends(get_db)):
    """批量导入事件(UPSERT,同日同名更新)。"""
    try:
        result = event_calendar_service.upsert_items(
            db, [item.model_dump() for item in body.items]
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@router.put("/{item_id}")
def update_event(item_id: int, body: EventCalendarUpdate, db: Session = Depends(get_db)):
    updates = body.model_dump(exclude_unset=True)
    try:
        row = event_calendar_service.update_item(db, item_id, updates)
    except LookupError as exc:
        raise HTTPException(404, "事件不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return event_calendar_service.to_response(row)


@router.delete("/{item_id}")
def delete_event(item_id: int, db: Session = Depends(get_db)):
    try:
        event_calendar_service.delete_item(db, item_id)
    except LookupError as exc:
        raise HTTPException(404, "事件不存在") from exc
    return {"deleted": item_id}
