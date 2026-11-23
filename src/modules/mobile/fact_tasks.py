"""Durable, device-scoped fact tasks. Reading a task never starts work."""

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from src.modules.mobile.fact_snapshots import (
    FactPhase, FactSnapshot, FactTarget, Symbol, capture_snapshot, symbol_in_watchlist,
)
from src.modules.mobile.notifications import publish_fact_completion
from src.modules.mobile.schemas import MobileResponse
from src.modules.mobile.symbol_facts import Market
from src.platform.persistence.database import get_db
from src.platform.persistence.models import MobileDevice, MobileFactTask
from src.platform.runtime.config import Settings
from src.web.errors import api_error


logger = logging.getLogger(__name__)
DB = Annotated[Session, Depends(get_db)]
_LEASE_SECONDS = 120
_HISTORY_PAGE_SIZE = 30


class FactTaskRequest(BaseModel):
    phase: FactPhase
    target: FactTarget | None = None


class FactTaskSummary(BaseModel):
    id: UUID
    phase: FactPhase
    target: FactTarget | None
    state: Literal["running", "retryable", "complete", "failed"]
    created_at: int
    created_on: date
    finished_at: int | None


class FactTask(FactTaskSummary):
    result: FactSnapshot | None


class FactTaskHistoryPage(BaseModel):
    items: list[FactTaskSummary]
    next_before: UUID | None


def _now() -> int:
    return int(time.time())


def _local_day(now: int) -> date:
    try:
        zone = ZoneInfo(Settings().app_timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    return datetime.fromtimestamp(now, zone).date()


def _target(row: MobileFactTask) -> FactTarget | None:
    return FactTarget(market=row.market, symbol=row.symbol) if row.market is not None else None


def _summary(row: MobileFactTask, now: int) -> FactTaskSummary:
    state = row.state
    if state == "running" and (row.lease_until is None or row.lease_until <= now):
        state = "retryable"
    return FactTaskSummary(
        id=row.id, phase=row.phase, target=_target(row), state=state,
        created_at=row.created_at, created_on=row.created_on, finished_at=row.finished_at,
    )


def _view(row: MobileFactTask, now: int) -> FactTask:
    return FactTask(**_summary(row, now).model_dump(), result=row.result)


def _history_query(device_id: str, phase: FactPhase | None, market: Market | None, symbol: str | None):
    if (market is None) != (symbol is None):
        raise api_error(422, "mobile_symbol_identity_required", "请同时提供市场和代码。")
    query = select(MobileFactTask).where(MobileFactTask.device_id == device_id)
    if phase is not None:
        query = query.where(MobileFactTask.phase == phase)
    if market is not None:
        query = query.where(MobileFactTask.market == market, MobileFactTask.symbol == symbol)
    return query.order_by(MobileFactTask.created_at.desc(), MobileFactTask.id.desc())


def _not_found():
    return api_error(404, "mobile_fact_task_not_found", "事实任务不可用。")


def _capture(bind, task_id: str, lease: str, get_portfolio_summary: Callable[[Session], dict]) -> None:
    payload = None
    try:
        with Session(bind) as db:
            row = db.get(MobileFactTask, task_id)
            if row is None or row.state != "running" or row.lease_token != lease:
                return
            device = db.get(MobileDevice, row.device_id)
            if device is not None and device.revoked_at is None and device.expires_at > _now():
                request = FactTaskRequest(phase=row.phase, target=_target(row))
                payload = capture_snapshot(db, request.phase, request.target, get_portfolio_summary).model_dump(mode="json")
    except Exception as exc:
        logger.warning("Mobile fact snapshot failed (%s)", type(exc).__name__)

    with Session(bind) as db:
        finished = db.execute(update(MobileFactTask).where(
            MobileFactTask.id == task_id, MobileFactTask.state == "running", MobileFactTask.lease_token == lease,
        ).values(
            state="complete" if payload is not None else "failed", result=payload,
            finished_at=_now(), lease_token=None, lease_until=None,
        ))
        if payload is not None and finished.rowcount == 1:
            publish_fact_completion(db, db.get_one(MobileFactTask, task_id))
        db.commit()


def create_fact_tasks_router(require_device, get_portfolio_summary: Callable[[Session], dict]) -> APIRouter:
    router = APIRouter()
    Device = Annotated[MobileDevice, Depends(require_device)]

    @router.put("/facts/tasks/{task_id}")
    def start(
        task_id: UUID, body: FactTaskRequest, device: Device, db: DB, background: BackgroundTasks,
    ) -> MobileResponse[FactTask]:
        key, owner, now = str(task_id), device.id, _now()
        market = body.target.market if body.target else None
        symbol = body.target.symbol if body.target else None
        created = db.execute(insert(MobileFactTask).values(
            id=key, device_id=owner, phase=body.phase, market=market, symbol=symbol,
            state="running", created_at=now, created_on=_local_day(now),
        ).on_conflict_do_nothing(index_elements=["id"])).rowcount
        row = db.get(MobileFactTask, key)
        if row is None or row.device_id != owner:
            raise _not_found()
        if (row.phase, row.market, row.symbol) != (body.phase, market, symbol):
            raise api_error(409, "mobile_fact_task_conflict", "此编号已有其他事实任务。")
        if created and body.target and not symbol_in_watchlist(db, body.target):
            raise api_error(404, "mobile_symbol_not_found", "此标的不可用。")

        lease = uuid4().hex
        claimed = db.execute(update(MobileFactTask).where(
            MobileFactTask.id == key, MobileFactTask.state == "running",
            or_(MobileFactTask.lease_until.is_(None), MobileFactTask.lease_until <= now),
        ).values(lease_token=lease, lease_until=now + _LEASE_SECONDS)).rowcount
        db.commit()
        if claimed:
            background.add_task(_capture, db.get_bind(), key, lease, get_portfolio_summary)
        return MobileResponse(data=_view(row, now))

    @router.get("/facts/tasks")
    def history(
        device: Device, db: DB, on: date | None = None, phase: FactPhase | None = None,
        market: Market | None = None, symbol: Annotated[Symbol | None, Query()] = None,
    ) -> MobileResponse[list[FactTaskSummary]]:
        query = _history_query(device.id, phase, market, symbol)
        if on is not None:
            query = query.where(MobileFactTask.created_on == on)
        rows = db.scalars(query.limit(_HISTORY_PAGE_SIZE))
        now = _now()
        return MobileResponse(data=[_summary(row, now) for row in rows])

    @router.get("/facts/task-history")
    def history_page(
        device: Device, db: DB, start_on: date | None = None, end_on: date | None = None,
        phase: FactPhase | None = None, market: Market | None = None,
        symbol: Annotated[Symbol | None, Query()] = None, before: UUID | None = None,
    ) -> MobileResponse[FactTaskHistoryPage]:
        if start_on is not None and end_on is not None and start_on > end_on:
            raise api_error(422, "mobile_fact_history_date_order", "开始日期必须早于或等于截止日期。")
        query = _history_query(device.id, phase, market, symbol)
        if start_on is not None:
            query = query.where(MobileFactTask.created_on >= start_on)
        if end_on is not None:
            query = query.where(MobileFactTask.created_on <= end_on)
        if before is not None:
            cursor = str(before)
            anchor = db.scalar(query.where(MobileFactTask.id == cursor).with_only_columns(MobileFactTask.created_at))
            if anchor is None:
                raise api_error(422, "mobile_fact_history_cursor_invalid", "请重新读取事实任务历史。")
            query = query.where(or_(
                MobileFactTask.created_at < anchor,
                and_(MobileFactTask.created_at == anchor, MobileFactTask.id < cursor),
            ))
        rows = list(db.scalars(query.limit(_HISTORY_PAGE_SIZE + 1)))
        now = _now()
        items = [_summary(row, now) for row in rows[:_HISTORY_PAGE_SIZE]]
        return MobileResponse(data=FactTaskHistoryPage(
            items=items, next_before=items[-1].id if len(rows) > _HISTORY_PAGE_SIZE else None,
        ))

    @router.get("/facts/tasks/{task_id}")
    def detail(task_id: UUID, device: Device, db: DB) -> MobileResponse[FactTask]:
        row = db.get(MobileFactTask, str(task_id))
        if row is None or row.device_id != device.id:
            raise _not_found()
        return MobileResponse(data=_view(row, _now()))

    return router
