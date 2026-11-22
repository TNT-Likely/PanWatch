"""Scoped mobile access to the existing Web price rules and persisted hits."""

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, FiniteFloat, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from src.modules.market import price_alert_service
from src.modules.mobile.push import push_delivery_states, push_event
from src.modules.mobile.schemas import MobileResponse, WatchlistSymbol
from src.platform.persistence.database import get_db
from src.platform.persistence.models import MobileDevice, PriceAlertHit, PriceAlertRule, Stock
from src.web.errors import api_error


DB = Annotated[Session, Depends(get_db)]
PushDelivery = Literal["pending", "submitted", "failed"]
_PUSH_DELIVERY: dict[str, PushDelivery] = {
    "pending": "pending", "retry": "pending", "sending": "pending",
    "delivered": "submitted", "failed": "failed", "blocked": "failed",
}


class AlertCondition(BaseModel):
    type: str
    op: str
    value: FiniteFloat | list[FiniteFloat]


class ConditionGroup(BaseModel):
    op: Literal["and", "or"]
    items: list[AlertCondition]


class AlertRule(BaseModel):
    id: int
    name: str
    target: WatchlistSymbol
    enabled: bool
    condition_group: ConditionGroup | None


class CreateAlert(BaseModel):
    request_id: UUID
    market: Literal["CN", "HK", "US"]
    symbol: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    comparison: Literal["<", ">"]
    price: FiniteFloat = Field(gt=0)


class AlertEnabled(BaseModel):
    enabled: bool


class EventCondition(BaseModel):
    type: str
    op: str
    target: FiniteFloat | list[FiniteFloat] | None
    actual: FiniteFloat | None
    matched: bool


class AlertEvent(BaseModel):
    id: int
    rule_id: int
    rule_name: str
    target: WatchlistSymbol
    occurred_at: datetime
    observed_price: FiniteFloat | None
    quote_source: str | None
    group_op: Literal["and", "or"]
    conditions: list[EventCondition]
    push_delivery: PushDelivery | None = None


def _rule(row: PriceAlertRule) -> AlertRule:
    try:
        group = price_alert_service.validate_condition_group(row.condition_group)
    except ValueError:
        group = None
    return AlertRule(
        id=row.id, name=row.name, target=WatchlistSymbol.model_validate(row.stock, from_attributes=True),
        enabled=bool(row.enabled), condition_group=group,
    )


def _number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _event(hit: PriceAlertHit, delivery: str | None = None) -> AlertEvent:
    snapshot = hit.trigger_snapshot or {}
    quote = snapshot.get("quote") or {}
    conditions = []
    for detail in snapshot.get("conditions", []):
        if not isinstance(detail, dict) or detail.get("type") not in price_alert_service.ALERT_CONDITION_TYPES:
            continue
        op = detail.get("op")
        if op not in price_alert_service.ALERT_CONDITION_OPERATORS:
            continue
        target = detail.get("target")
        if isinstance(target, list):
            values = [_number(value) for value in target]
            target = values if all(value is not None for value in values) else None
        else:
            target = _number(target)
        conditions.append(EventCondition(
            type=detail["type"], op=op, target=target, actual=_number(detail.get("actual")),
            matched=detail.get("matched") is True,
        ))
    return AlertEvent(
        id=hit.id, rule_id=hit.rule_id, rule_name=hit.rule.name,
        target=WatchlistSymbol.model_validate(hit.stock, from_attributes=True),
        occurred_at=hit.trigger_time.replace(tzinfo=UTC), observed_price=_number(quote.get("current_price")),
        quote_source=quote.get("source") if isinstance(quote.get("source"), str) else None,
        group_op="or" if snapshot.get("group_op") == "or" else "and", conditions=conditions,
        push_delivery=_PUSH_DELIVERY.get(delivery or ""),
    )


def create_alert_router(require_device: Callable[..., MobileDevice]) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_device)])

    @router.get("/alerts")
    def rules(db: DB) -> MobileResponse[list[AlertRule]]:
        rows = price_alert_service.list_alert_rules(db, limit=None)
        return MobileResponse(data=[_rule(row) for row in rows])

    @router.post("/alerts", status_code=201)
    def create(body: CreateAlert, db: DB, device: Annotated[MobileDevice, Depends(require_device)]) -> MobileResponse[AlertRule]:
        stock = db.scalar(select(Stock).where(Stock.market == body.market, Stock.symbol == body.symbol).order_by(Stock.id).limit(1))
        if stock is None:
            raise api_error(404, "price_alert_stock_not_found", "自选标的不存在。")
        group = {"op": "and", "items": [{"type": "price", "op": body.comparison, "value": body.price}]}
        key = f"{device.id}:{body.request_id}"
        lookup = select(PriceAlertRule).where(PriceAlertRule.mobile_request_id == key)
        row = db.scalar(lookup)
        if row is None:
            try:
                row = price_alert_service.create_alert_rule(
                    db, stock_id=stock.id, condition_group=group, mobile_request_id=key,
                )
            except IntegrityError:
                db.rollback()
                row = db.scalar(lookup)
                if row is None:
                    raise
        if row.stock_id != stock.id or row.condition_group != group:
            raise api_error(409, "mobile_alert_request_conflict", "此请求已用于其他条件。")
        return MobileResponse(data=_rule(row))

    @router.post("/alerts/{rule_id}/enabled")
    def set_enabled(rule_id: int, body: AlertEnabled, db: DB) -> MobileResponse[AlertRule]:
        try:
            row = price_alert_service.update_alert_rule(db, rule_id, {"enabled": body.enabled})
        except LookupError as exc:
            raise api_error(404, "price_alert_not_found", "规则不存在。") from exc
        return MobileResponse(data=_rule(row))

    @router.get("/events")
    def history(
        db: DB, device: Annotated[MobileDevice, Depends(require_device)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> MobileResponse[list[AlertEvent]]:
        rows = db.scalars(
            select(PriceAlertHit).options(joinedload(PriceAlertHit.stock), joinedload(PriceAlertHit.rule))
            .order_by(PriceAlertHit.trigger_time.desc(), PriceAlertHit.id.desc()).limit(limit)
        ).all()
        receipts = push_delivery_states(db, device.id, [row.id for row in rows])
        return MobileResponse(data=[_event(row, receipts.get(row.id)) for row in rows])

    @router.get("/events/{event_id}")
    def get_event(event_id: int, db: DB, device: Annotated[MobileDevice, Depends(require_device)]) -> MobileResponse[AlertEvent]:
        hit = db.get(PriceAlertHit, event_id)
        if hit is None:
            raise api_error(404, "mobile_alert_event_not_found", "提醒事件已不可用。")
        receipt = push_delivery_states(db, device.id, [event_id]).get(event_id)
        return MobileResponse(data=_event(hit, receipt))

    @router.get("/push/events/{event_id}")
    def notification_event(
        event_id: UUID, route_id: UUID, db: DB, device: Annotated[MobileDevice, Depends(require_device)],
    ) -> MobileResponse[AlertEvent]:
        result = push_event(db, device.id, event_id, route_id)
        if result is None:
            raise api_error(404, "mobile_alert_event_not_found", "此设备的通知已不可用。")
        return MobileResponse(data=_event(*result))

    return router
