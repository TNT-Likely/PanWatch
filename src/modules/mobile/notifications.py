"""Project only mobile-safe alert and fact-task events from the service inbox."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, PositiveInt
from sqlalchemy import and_, func, literal, or_, select, tuple_
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from src.modules.mobile.fact_snapshots import FactPhase
from src.modules.mobile.schemas import MobileResponse
from src.modules.notifications.schemas import NotificationSelection
from src.modules.notifications.service import NotificationService, utc
from src.platform.persistence.database import get_db
from src.platform.persistence.models import MobileDevice, MobileFactTask, NotificationEvent, NotificationReceipt
from src.web.errors import api_error


DB = Annotated[Session, Depends(get_db)]
_FACT_EVENT = "mobile_fact_complete"


class AlertTarget(BaseModel):
    kind: Literal["alert"] = "alert"
    event_id: PositiveInt
    title: str
    market: str
    symbol: str


class FactTarget(BaseModel):
    kind: Literal["fact_task"] = "fact_task"
    task_id: UUID
    phase: FactPhase


class InboxItem(BaseModel):
    id: PositiveInt
    occurred_at: datetime
    read_at: datetime | None
    target: AlertTarget | FactTarget


class InboxPage(BaseModel):
    items: list[InboxItem]
    observed_id: int
    next_before: int | None


def _recipient(device_id: str) -> str:
    return f"mobile:{device_id}"


def publish_fact_completion(db: Session, task: MobileFactTask) -> None:
    NotificationService(db).publish(
        source="mobile", event_type=_FACT_EVENT, dedupe_key=f"mobile_fact_complete:{task.id}",
        subject_kind="mobile_fact_task", subject_id=task.id, title="",
        action={"kind": "mobile_fact_task", "task_id": task.id}, template_params={"phase": task.phase},
        occurred_at=datetime.fromtimestamp(task.finished_at, UTC), toast_eligible=False, recipient_key=_recipient(task.device_id),
    )


def _inbox(device: MobileDevice, alerts_scope: str):
    fact = and_(
        NotificationEvent.source == "mobile", NotificationEvent.event_type == _FACT_EVENT,
        NotificationReceipt.id.is_not(None),
    )
    visible = fact
    if alerts_scope in device.scope.split():
        visible = or_(fact, and_(
            NotificationEvent.source == "market", NotificationEvent.event_type == "price_alert_hit",
            NotificationEvent.subject_kind == "alert_hit",
        ))
    return (
        select(NotificationEvent, NotificationReceipt.read_at)
        .outerjoin(NotificationReceipt, and_(
            NotificationReceipt.notification_id == NotificationEvent.id,
            NotificationReceipt.recipient_key == _recipient(device.id),
        ))
        .where(visible)
    )


def _item(event: NotificationEvent, read_at: datetime | None) -> InboxItem:
    params = event.template_params
    if event.event_type == _FACT_EVENT:
        target = FactTarget(task_id=UUID(event.subject_id), phase=params["phase"])
    else:
        target = AlertTarget(
            event_id=int(event.subject_id), title=event.display_snapshot["title"],
            market=params["market"], symbol=params["symbol"],
        )
    return InboxItem(id=event.id, occurred_at=utc(event.occurred_at), read_at=utc(read_at), target=target)


def create_notifications_router(require_device: Callable[..., MobileDevice], alerts_scope: str) -> APIRouter:
    router = APIRouter(prefix="/notifications")
    Device = Annotated[MobileDevice, Depends(require_device)]

    @router.get("")
    def inbox(
        db: DB, device: Device, limit: Annotated[int, Query(ge=1, le=50)] = 30,
        before: Annotated[int | None, Query(gt=0)] = None, through: Annotated[int | None, Query(gt=0)] = None,
    ) -> MobileResponse[InboxPage]:
        query = _inbox(device, alerts_scope)
        observed = through if through is not None else db.scalar(query.with_only_columns(func.max(NotificationEvent.id))) or 0
        query = query.where(NotificationEvent.id <= observed)
        if before is not None:
            anchor = query.with_only_columns(NotificationEvent.occurred_at).where(NotificationEvent.id == before)
            if db.scalar(anchor) is None:
                raise api_error(404, "mobile_notification_cursor_expired", "通知列表有更新，请重新读取。")
            # Keep the stored timestamp so old SQLite rows with whole seconds compare correctly.
            query = query.where(tuple_(NotificationEvent.occurred_at, NotificationEvent.id) < tuple_(
                anchor.correlate(None).scalar_subquery(), before,
            ))
        rows = db.execute(query.order_by(NotificationEvent.occurred_at.desc(), NotificationEvent.id.desc()).limit(limit + 1)).all()
        items = [_item(event, read_at) for event, read_at in rows[:limit]]
        return MobileResponse(data=InboxPage(
            items=items, observed_id=observed, next_before=items[-1].id if len(rows) > limit else None,
        ))

    @router.get("/unread")
    def has_unread(db: DB, device: Device) -> MobileResponse[bool]:
        unread = _inbox(device, alerts_scope).where(
            NotificationReceipt.read_at.is_(None),
        ).with_only_columns(NotificationEvent.id)
        return MobileResponse(data=bool(db.scalar(select(unread.exists()))))

    @router.post("/read")
    def mark_read(body: NotificationSelection, db: DB, device: Device) -> MobileResponse[bool]:
        if body.source is not None or body.view != "all":
            raise api_error(422, "mobile_notification_selection_invalid", "请选择收件箱内的通知。")
        stamp = datetime.now(UTC)
        selected = NotificationEvent.id <= body.through_id if body.through_id else NotificationEvent.id.in_(body.ids)
        query = _inbox(device, alerts_scope).where(selected, NotificationReceipt.read_at.is_(None)).with_only_columns(
            NotificationEvent.id, literal(_recipient(device.id)), literal(stamp),
        )
        db.execute(insert(NotificationReceipt).from_select(["notification_id", "recipient_key", "read_at"], query)
                   .on_conflict_do_update(
            index_elements=[NotificationReceipt.notification_id, NotificationReceipt.recipient_key],
            set_={"read_at": stamp}, where=NotificationReceipt.read_at.is_(None),
        ))
        db.commit()
        return MobileResponse(data=True)

    return router
