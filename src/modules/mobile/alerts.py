"""Scoped mobile access to the existing Web price rules and persisted hits."""

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, FiniteFloat, StringConstraints
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from src.modules.market import price_alert_service
from src.modules.market.monitoring_health import monitoring_health
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
    editable_price: bool
    last_trigger_at: datetime | None


class AlertScan(BaseModel):
    status: str
    last_completed_at: datetime | None
    next_scan_at: datetime | None


class RuleCheck(BaseModel):
    rule_id: int
    status: str
    last_checked_at: datetime | None


class AlertHealth(BaseModel):
    scan: AlertScan
    items: list[RuleCheck]


class CreateAlert(BaseModel):
    request_id: UUID
    market: Literal["CN", "HK", "US"]
    symbol: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    comparison: Literal["<", ">"]
    price: FiniteFloat = Field(gt=0)


class AlertEnabled(BaseModel):
    enabled: bool


class ChangePrice(BaseModel):
    price: FiniteFloat = Field(gt=0)
    previous_price: FiniteFloat = Field(gt=0)
    comparison: Literal["<", ">", "<=", ">="]


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


class AlertHistoryPage(BaseModel):
    items: list[AlertEvent]
    next_before: int | None


def _simple_threshold(group: ConditionGroup | None) -> AlertCondition | None:
    if not group or len(group.items) != 1:
        return None
    only = group.items[0]
    return only if only.type == "price" and only.op in {"<", ">", "<=", ">="} and isinstance(only.value, float) and only.value > 0 else None


def _rule(row: PriceAlertRule) -> AlertRule:
    try:
        group = ConditionGroup.model_validate(price_alert_service.validate_condition_group(row.condition_group))
    except ValueError:
        group = None
    return AlertRule(
        id=row.id, name=row.name, target=WatchlistSymbol.model_validate(row.stock, from_attributes=True),
        enabled=bool(row.enabled), condition_group=group, editable_price=_simple_threshold(group) is not None,
        last_trigger_at=row.last_trigger_at.replace(tzinfo=UTC) if row.last_trigger_at else None,
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


def _history_rows(
    db: Session, market: str | None, symbol: str | None, rule_id: int | None, limit: int, before: int | None = None,
) -> list[PriceAlertHit]:
    if (market is None) != (symbol is None):
        raise api_error(422, "mobile_symbol_identity_required", "请同时提供市场和代码。")
    query = select(PriceAlertHit)
    if market is not None:
        query = query.join(PriceAlertHit.stock).where(Stock.market == market, Stock.symbol == symbol)
    if rule_id is not None:
        query = query.where(PriceAlertHit.rule_id == rule_id)
    if before is not None:
        anchor = query.with_only_columns(PriceAlertHit.trigger_time).where(PriceAlertHit.id == before)
        if db.scalar(anchor) is None:
            raise api_error(404, "mobile_alert_history_cursor_expired", "触发记录有更新，请重新读取。")
        # Compare against the stored timestamp so SQLite's legacy whole-second rows keep their ordering.
        query = query.where(tuple_(PriceAlertHit.trigger_time, PriceAlertHit.id) < tuple_(
            anchor.correlate(None).scalar_subquery(), before,
        ))
    query = query.options(joinedload(PriceAlertHit.stock), joinedload(PriceAlertHit.rule))
    return list(db.scalars(query.order_by(PriceAlertHit.trigger_time.desc(), PriceAlertHit.id.desc()).limit(limit)))


def _history_events(db: Session, device: MobileDevice, rows: list[PriceAlertHit]) -> list[AlertEvent]:
    receipts = push_delivery_states(db, device.id, [row.id for row in rows])
    return [_event(row, receipts.get(row.id)) for row in rows]


def create_alert_router(require_device: Callable[..., MobileDevice]) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_device)])

    @router.get("/alerts")
    def rules(db: DB) -> MobileResponse[list[AlertRule]]:
        rows = price_alert_service.list_alert_rules(db, limit=None)
        return MobileResponse(data=[_rule(row) for row in rows])

    @router.get("/alerts/health")
    def health(db: DB) -> MobileResponse[AlertHealth]:
        return MobileResponse(data=AlertHealth.model_validate(monitoring_health(db)))

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
                    db, stock_id=stock.id, condition_group=group, repeat_mode="once", mobile_request_id=key,
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

    @router.put("/alerts/{rule_id}/price")
    def change_price(rule_id: int, body: ChangePrice, db: DB) -> MobileResponse[AlertRule]:
        row = db.get(PriceAlertRule, rule_id)
        if row is None:
            raise api_error(404, "price_alert_not_found", "规则不存在。")
        condition = _simple_threshold(_rule(row).condition_group)
        if condition is None:
            raise api_error(409, "mobile_alert_edit_requires_web", "请在网页编辑此规则。")
        if condition.op != body.comparison or condition.value != body.previous_price:
            raise api_error(409, "mobile_alert_changed", "规则已变更，请重新读取。")
        if condition.value == body.price:
            return MobileResponse(data=_rule(row))
        original = row.condition_group
        replacement = {**original, "items": [{**original["items"][0], "value": body.price}]}
        changed = db.execute(update(PriceAlertRule).where(
            PriceAlertRule.id == rule_id, func.json(PriceAlertRule.condition_group) == func.json(json.dumps(original)),
        ).values(condition_group=replacement, trigger_count_today=0, trigger_date="").execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise api_error(409, "mobile_alert_changed", "规则已变更，请重新读取。")
        db.commit()
        db.refresh(row)
        return MobileResponse(data=_rule(row))

    @router.delete("/alerts/{rule_id}")
    def delete(rule_id: int, db: DB) -> MobileResponse[bool]:
        try:
            price_alert_service.delete_alert_rule(db, rule_id)
        except LookupError:
            pass
        return MobileResponse(data=True)

    @router.get("/events")
    def history(
        db: DB, device: Annotated[MobileDevice, Depends(require_device)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        market: Literal["CN", "HK", "US"] | None = None,
        symbol: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        rule_id: Annotated[int | None, Query(gt=0)] = None,
    ) -> MobileResponse[list[AlertEvent]]:
        rows = _history_rows(db, market, symbol, rule_id, limit)
        return MobileResponse(data=_history_events(db, device, rows))

    @router.get("/alerts/history")
    def history_page(
        db: DB, device: Annotated[MobileDevice, Depends(require_device)],
        limit: Annotated[int, Query(ge=1, le=50)] = 30,
        market: Literal["CN", "HK", "US"] | None = None,
        symbol: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        rule_id: Annotated[int | None, Query(gt=0)] = None,
        before: Annotated[int | None, Query(gt=0)] = None,
    ) -> MobileResponse[AlertHistoryPage]:
        rows = _history_rows(db, market, symbol, rule_id, limit + 1, before)
        items = _history_events(db, device, rows[:limit])
        return MobileResponse(data=AlertHistoryPage(
            items=items, next_before=items[-1].id if len(rows) > limit else None,
        ))

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
