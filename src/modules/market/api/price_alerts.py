import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from src.platform.runtime.config import Settings
from src.modules.market import price_alert_service
from src.modules.market.price_alert_engine import ENGINE
from src.platform.persistence.database import get_db
from src.platform.persistence.models import PriceAlertHit, PriceAlertRule, Stock

logger = logging.getLogger(__name__)
router = APIRouter()


def _format_datetime(dt) -> str:
    """格式化时间为当前时区的 ISO 格式（naive datetime 视为 UTC）。"""
    if not dt:
        return ""
    tz_name = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo).isoformat(timespec="seconds")


class AlertConditionItem(BaseModel):
    type: str = Field(..., description="price/change_pct/turnover/volume/volume_ratio")
    op: str = Field(..., description=">=/<=/>/</==/between")
    value: float | list[float] = Field(..., description="阈值")


class AlertConditionGroup(BaseModel):
    op: str = Field(default="and", description="and/or")
    items: list[AlertConditionItem] = Field(default_factory=list)


class PriceAlertCreate(BaseModel):
    stock_id: int
    name: str = ""
    enabled: bool = True
    condition_group: AlertConditionGroup
    market_hours_mode: str = "trading_only"
    cooldown_minutes: int = 30
    max_triggers_per_day: int = 3
    repeat_mode: str = "repeat"
    expire_at: str | None = None
    notify_channel_ids: list[int] = []


class PriceAlertUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    condition_group: AlertConditionGroup | None = None
    market_hours_mode: str | None = None
    cooldown_minutes: int | None = None
    max_triggers_per_day: int | None = None
    repeat_mode: str | None = None
    expire_at: str | None = None
    notify_channel_ids: list[int] | None = None


class ToggleBody(BaseModel):
    enabled: bool


def _to_response(rule: PriceAlertRule) -> dict:
    stock = rule.stock
    return {
        "id": rule.id,
        "stock_id": rule.stock_id,
        "stock_symbol": stock.symbol if stock else "",
        "stock_name": stock.name if stock else "",
        "market": stock.market if stock else "",
        "name": rule.name,
        "enabled": rule.enabled,
        "condition_group": rule.condition_group or {},
        "market_hours_mode": rule.market_hours_mode,
        "cooldown_minutes": rule.cooldown_minutes,
        "max_triggers_per_day": rule.max_triggers_per_day,
        "repeat_mode": rule.repeat_mode,
        "expire_at": _format_datetime(rule.expire_at) or None,
        "notify_channel_ids": rule.notify_channel_ids or [],
        "last_trigger_at": _format_datetime(rule.last_trigger_at) or None,
        "last_trigger_price": rule.last_trigger_price,
        "trigger_count_today": rule.trigger_count_today or 0,
        "trigger_date": rule.trigger_date or "",
        "created_at": _format_datetime(rule.created_at),
        "updated_at": _format_datetime(rule.updated_at),
    }


@router.get("")
def list_alert_rules(db: Session = Depends(get_db)):
    rows = price_alert_service.list_alert_rules(db, limit=None)
    return [_to_response(r) for r in rows]


@router.post("")
def create_alert_rule(body: PriceAlertCreate, db: Session = Depends(get_db)):
    try:
        row = price_alert_service.create_alert_rule(
            db,
            stock_id=body.stock_id,
            name=body.name,
            enabled=body.enabled,
            condition_group=body.condition_group.model_dump(),
            market_hours_mode=body.market_hours_mode,
            cooldown_minutes=body.cooldown_minutes,
            max_triggers_per_day=body.max_triggers_per_day,
            repeat_mode=body.repeat_mode,
            expire_at=body.expire_at,
            notify_channel_ids=body.notify_channel_ids,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _to_response(row)


@router.put("/{rule_id}")
def update_alert_rule(rule_id: int, body: PriceAlertUpdate, db: Session = Depends(get_db)):
    updates = body.model_dump(exclude_unset=True)
    try:
        row = price_alert_service.update_alert_rule(db, rule_id, updates)
    except LookupError as exc:
        raise HTTPException(404, "规则不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _to_response(row)


@router.post("/{rule_id}/toggle")
def toggle_alert_rule(rule_id: int, body: ToggleBody, db: Session = Depends(get_db)):
    row = db.query(PriceAlertRule).filter(PriceAlertRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "规则不存在")
    row.enabled = bool(body.enabled)
    db.commit()
    db.refresh(row)
    return _to_response(row)


@router.delete("/{rule_id}")
def delete_alert_rule(rule_id: int, db: Session = Depends(get_db)):
    try:
        price_alert_service.delete_alert_rule(db, rule_id)
    except LookupError as exc:
        raise HTTPException(404, "规则不存在") from exc
    return {"ok": True}


@router.get("/hits/today")
def list_today_hits(limit: int = 50, db: Session = Depends(get_db)):
    """今日(本地时区)全部命中,跨规则聚合 —— 供首页"今日要紧事"。"""
    tz_name = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc
    local_midnight = datetime.now(tzinfo).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = local_midnight.astimezone(timezone.utc).replace(tzinfo=None)

    hits = (
        db.query(PriceAlertHit)
        .filter(PriceAlertHit.trigger_time >= start_utc)
        .order_by(PriceAlertHit.trigger_time.desc(), PriceAlertHit.id.desc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )
    if not hits:
        return []
    rule_map = {r.id: r for r in db.query(PriceAlertRule).all()}
    stock_ids = {h.stock_id for h in hits}
    stock_map = {s.id: s for s in db.query(Stock).filter(Stock.id.in_(stock_ids)).all()}
    out = []
    for h in hits:
        stock = stock_map.get(h.stock_id)
        rule = rule_map.get(h.rule_id)
        out.append(
            {
                "rule_id": h.rule_id,
                "rule_name": (rule.name if rule else "") or "提醒",
                "symbol": stock.symbol if stock else "",
                "name": stock.name if stock else "",
                "market": stock.market if stock else "CN",
                "trigger_time": _format_datetime(h.trigger_time),
                "snapshot": h.trigger_snapshot or {},
            }
        )
    return out


@router.get("/{rule_id}/hits")
def list_alert_hits(rule_id: int, limit: int = 50, db: Session = Depends(get_db)):
    _ = db.query(PriceAlertRule).filter(PriceAlertRule.id == rule_id).first()
    rows = (
        db.query(PriceAlertHit)
        .filter(PriceAlertHit.rule_id == rule_id)
        .order_by(PriceAlertHit.trigger_time.desc(), PriceAlertHit.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return [
        {
            "id": r.id,
            "rule_id": r.rule_id,
            "stock_id": r.stock_id,
            "trigger_time": _format_datetime(r.trigger_time),
            "trigger_snapshot": r.trigger_snapshot or {},
            "notify_success": bool(r.notify_success),
            "notify_error": r.notify_error or "",
        }
        for r in rows
    ]


@router.post("/{rule_id}/test")
async def test_alert_rule(rule_id: int):
    result = await ENGINE.scan_once(
        only_rule_id=rule_id, dry_run=True, bypass_market_hours=True
    )
    return result


@router.post("/scan")
async def scan_alert_rules(dry_run: bool = False, bypass_market_hours: bool = True):
    try:
        from server import price_alert_scheduler

        if price_alert_scheduler:
            # 手动扫描默认绕过交易时段门禁，便于即时验证规则
            if bypass_market_hours:
                return await price_alert_scheduler.trigger_once(dry_run=dry_run)
            return await ENGINE.scan_once(dry_run=dry_run, bypass_market_hours=False)
    except Exception:
        pass
    return await ENGINE.scan_once(
        dry_run=dry_run, bypass_market_hours=bypass_market_hours
    )
