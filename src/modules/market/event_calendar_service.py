"""事件日历的共享领域服务。

HTTP API 与导入脚本共用这里,避免两条入口各自维护校验、日期解析与
UPSERT 逻辑(分层仿 price_alert_service + api/price_alerts)。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from src.platform.persistence.models import EventCalendarItem

logger = logging.getLogger(__name__)

EVENT_LEVELS = {"high", "medium", "low"}
EVENT_DIRECTIONS = {"bullish", "bearish", "neutral", ""}


def parse_event_date(value: Any) -> date:
    """解析 YYYY-MM-DD;datetime/date 原样,其余字符串严格按前 10 位解析。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) < 10:
        raise ValueError(f"event_date 格式错误: {value!r}(需要 YYYY-MM-DD)")
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"event_date 格式错误: {value!r}(需要 YYYY-MM-DD)") from exc


def _normalize_level(value: Any) -> str:
    level = str(value or "medium").strip().lower()
    if level not in EVENT_LEVELS:
        raise ValueError(f"level 仅支持 {sorted(EVENT_LEVELS)}: {value!r}")
    return level


def _normalize_direction(value: Any) -> str:
    direction = str(value or "").strip().lower()
    if direction not in EVENT_DIRECTIONS:
        raise ValueError(f"direction 仅支持 {sorted(EVENT_DIRECTIONS - {''})} 或空: {value!r}")
    return direction


def normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
    """校验并归一一条事件数据(JSON 可序列化纯 dict),失败抛 ValueError。"""
    if not isinstance(raw, dict):
        raise ValueError("事件条目必须是对象")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("name 不能为空")
    return {
        "event_date": parse_event_date(raw.get("event_date")),
        "level": _normalize_level(raw.get("level")),
        "name": name,
        "scope": str(raw.get("scope") or "").strip(),
        "expected": str(raw.get("expected") if raw.get("expected") is not None else "").strip(),
        "actual": str(raw.get("actual") if raw.get("actual") is not None else "").strip(),
        "direction": _normalize_direction(raw.get("direction")),
        "impact_boards": list(raw.get("impact_boards") or []),
        "meta": dict(raw.get("meta") or {}),
    }


def upsert_items(db: Session, items: list[dict[str, Any]]) -> dict[str, int]:
    """批量 UPSERT:按 (event_date, name) 匹配既有行,存在则更新,否则新增。

    Returns:
        {"created": 新增条数, "updated": 更新条数}
    """
    created = updated = 0
    for raw in items or []:
        data = normalize_item(raw)
        row = (
            db.query(EventCalendarItem)
            .filter(
                EventCalendarItem.event_date == data["event_date"],
                EventCalendarItem.name == data["name"],
            )
            .first()
        )
        if not row:
            row = EventCalendarItem(event_date=data["event_date"], name=data["name"])
            db.add(row)
            created += 1
        else:
            updated += 1
        row.level = data["level"]
        row.scope = data["scope"]
        row.expected = data["expected"]
        row.actual = data["actual"]
        row.direction = data["direction"]
        row.impact_boards = data["impact_boards"]
        row.meta = data["meta"]
    db.commit()
    return {"created": created, "updated": updated}


def update_item(db: Session, item_id: int, updates: dict[str, Any]) -> EventCalendarItem:
    """按 id 更新事件条目;不存在抛 LookupError,字段非法抛 ValueError。"""
    row = db.query(EventCalendarItem).filter(EventCalendarItem.id == item_id).first()
    if not row:
        raise LookupError("事件不存在")
    if "event_date" in updates and updates["event_date"] is not None:
        row.event_date = parse_event_date(updates["event_date"])
    if updates.get("level") is not None:
        row.level = _normalize_level(updates["level"])
    if updates.get("name") is not None:
        name = str(updates["name"]).strip()
        if not name:
            raise ValueError("name 不能为空")
        row.name = name
    for field in ("scope", "expected", "actual"):
        if updates.get(field) is not None:
            setattr(row, field, str(updates[field]).strip())
    if updates.get("direction") is not None:
        row.direction = _normalize_direction(updates["direction"])
    if updates.get("impact_boards") is not None:
        row.impact_boards = list(updates["impact_boards"] or [])
    if updates.get("meta") is not None:
        row.meta = dict(updates["meta"] or {})
    db.commit()
    db.refresh(row)
    return row


def delete_item(db: Session, item_id: int) -> None:
    """按 id 删除事件条目;不存在抛 LookupError。"""
    row = db.query(EventCalendarItem).filter(EventCalendarItem.id == item_id).first()
    if not row:
        raise LookupError("事件不存在")
    db.delete(row)
    db.commit()


def list_by_window(
    db: Session,
    *,
    start: str | date | None = None,
    end: str | date | None = None,
    level: str | None = None,
) -> list[EventCalendarItem]:
    """按日期窗列出事件(默认未来 30 天,窗内按日期升序)。"""
    today = date.today()
    start_d = parse_event_date(start) if start else today
    end_d = parse_event_date(end) if end else None
    if end_d is None:
        end_d = start_d if start else date.fromordinal(today.toordinal() + 30)
    query = db.query(EventCalendarItem).filter(
        EventCalendarItem.event_date >= start_d,
        EventCalendarItem.event_date <= end_d,
    )
    if level:
        query = query.filter(EventCalendarItem.level == _normalize_level(level))
    return query.order_by(EventCalendarItem.event_date.asc(), EventCalendarItem.id.asc()).all()


def to_response(row: EventCalendarItem) -> dict[str, Any]:
    """ORM 行 → JSON 响应(event_date 转 ISO 字符串)。"""
    return {
        "id": row.id,
        "event_date": row.event_date.isoformat() if row.event_date else "",
        "level": row.level,
        "name": row.name,
        "scope": row.scope or "",
        "expected": row.expected or "",
        "actual": row.actual or "",
        "direction": row.direction or "",
        "impact_boards": row.impact_boards or [],
        "meta": row.meta or {},
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }
