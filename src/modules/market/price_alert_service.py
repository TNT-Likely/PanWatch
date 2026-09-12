"""价格提醒规则的共享领域服务。

HTTP API 和 PanAgent 工具都通过这里读写提醒规则，避免两条入口各自维护
校验、条件转换和触发计数重置逻辑。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from src.platform.persistence.models import PriceAlertHit, PriceAlertRule, Stock


ALERT_CONDITION_TYPES = {"price", "change_pct", "turnover", "volume", "volume_ratio"}
ALERT_CONDITION_OPERATORS = {">=", "<=", ">", "<", "==", "=", "!=", "<>", "between", "in"}


def validate_condition_group(group: dict[str, Any]) -> dict[str, Any]:
    """Validate and copy a condition group into a JSON-safe plain mapping."""
    if not isinstance(group, dict):
        raise ValueError("condition_group 必须是对象")
    op = str(group.get("op") or "and").lower()
    if op not in {"and", "or"}:
        raise ValueError("condition_group.op 仅支持 and/or")
    items = group.get("items") or []
    if not isinstance(items, list) or not items:
        raise ValueError("condition_group.items 不能为空")

    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("condition_group.items 必须是对象列表")
        condition_type = str(item.get("type") or "").strip()
        operator = str(item.get("op") or "").strip()
        if condition_type not in ALERT_CONDITION_TYPES:
            raise ValueError(f"不支持的条件类型: {condition_type}")
        if operator not in ALERT_CONDITION_OPERATORS:
            raise ValueError(f"不支持的运算符: {operator}")
        value = item.get("value")
        if operator in {"between", "in"} and (
            not isinstance(value, list) or len(value) != 2
        ):
            raise ValueError(f"{condition_type} 的 {operator} 需要两个值")
        normalized_items.append(
            {"type": condition_type, "op": operator, "value": value}
        )
    return {"op": op, "items": normalized_items}


def parse_expire_at(value: str | datetime | None) -> datetime | None:
    """Parse the ISO-8601 expiry accepted by both API and agent callers."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("expire_at 格式错误") from exc


def list_alert_rules(
    db: Session,
    *,
    symbol: str | None = None,
    market: str | None = None,
    enabled: bool | None = None,
    limit: int | None = 20,
) -> list[PriceAlertRule]:
    """List rules with cheap indexed filters for assistant queries."""
    query = db.query(PriceAlertRule).join(Stock)
    if symbol:
        query = query.filter(Stock.symbol == symbol)
    if market:
        query = query.filter(Stock.market == market)
    if enabled is not None:
        query = query.filter(PriceAlertRule.enabled == enabled)
    query = query.order_by(PriceAlertRule.updated_at.desc(), PriceAlertRule.id.desc())
    if limit is not None:
        query = query.limit(max(1, min(int(limit), 50)))
    return query.all()


def create_alert_rule(
    db: Session,
    *,
    stock_id: int,
    name: str = "",
    enabled: bool = True,
    condition_group: dict[str, Any],
    market_hours_mode: str = "trading_only",
    cooldown_minutes: int = 30,
    max_triggers_per_day: int = 3,
    repeat_mode: str = "repeat",
    expire_at: str | datetime | None = None,
    notify_channel_ids: list[int] | None = None,
) -> PriceAlertRule:
    """Create one validated rule for both HTTP and assistant callers."""
    stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if stock is None:
        raise LookupError("股票不存在")
    group = validate_condition_group(condition_group)
    try:
        cooldown = int(cooldown_minutes)
        max_triggers = int(max_triggers_per_day)
    except (TypeError, ValueError) as exc:
        raise ValueError("冷却时间和每日最大触发次数必须是整数") from exc
    if cooldown < 0:
        raise ValueError("冷却时间必须是非负整数")
    if max_triggers < 0:
        raise ValueError("每日最大触发次数必须是非负整数")
    market_hours = str(market_hours_mode or "trading_only").strip().lower()
    if market_hours not in {"always", "trading_only"}:
        raise ValueError("market_hours_mode 只能是 always 或 trading_only")
    repeat = str(repeat_mode or "repeat").strip().lower()
    if repeat not in {"once", "repeat"}:
        raise ValueError("repeat_mode 只能是 once 或 repeat")

    row = PriceAlertRule(
        stock_id=stock_id,
        name=(str(name or "").strip() or f"{stock.name} 提醒"),
        enabled=bool(enabled),
        condition_group=group,
        market_hours_mode=market_hours,
        cooldown_minutes=cooldown,
        max_triggers_per_day=max_triggers,
        repeat_mode=repeat,
        expire_at=parse_expire_at(expire_at),
        notify_channel_ids=list(notify_channel_ids or []),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_alert_rule(db: Session, rule_id: int) -> PriceAlertRule | None:
    """Return one rule without raising at the persistence boundary."""
    return db.query(PriceAlertRule).filter(PriceAlertRule.id == rule_id).first()


def compact_alert_rule(rule: PriceAlertRule) -> dict[str, Any]:
    """Project a rule to the small fact set an LLM needs for follow-up calls."""
    stock = rule.stock
    items = (rule.condition_group or {}).get("items") or []
    price_item = next(
        (item for item in items if isinstance(item, dict) and item.get("type") == "price"),
        None,
    )
    operator = str(price_item.get("op") or "") if price_item else ""
    direction = "above" if operator in {">", ">="} else "below" if operator in {"<", "<="} else ""
    target_price = price_item.get("value") if price_item else None
    try:
        target_price = float(target_price) if target_price is not None else None
    except (TypeError, ValueError):
        target_price = None
    return {
        "rule_id": rule.id,
        "name": rule.name or "",
        "symbol": stock.symbol if stock else "",
        "stock_name": stock.name if stock else "",
        "market": stock.market if stock else "",
        "enabled": bool(rule.enabled),
        "direction": direction,
        "target_price": target_price,
        "cooldown_minutes": int(rule.cooldown_minutes or 0),
        "max_triggers_per_day": int(rule.max_triggers_per_day or 0),
        "repeat_mode": rule.repeat_mode or "repeat",
    }


def update_alert_rule(
    db: Session,
    rule_id: int,
    updates: dict[str, Any],
) -> PriceAlertRule:
    """Apply assistant-friendly fields and reset the daily trigger window."""
    rule = get_alert_rule(db, rule_id)
    if rule is None:
        raise LookupError("价格提醒不存在")
    if not updates:
        raise ValueError("至少提供一个要修改的字段")

    allowed = {
        "name",
        "enabled",
        "condition_group",
        "direction",
        "target_price",
        "cooldown_minutes",
        "max_triggers_per_day",
        "repeat_mode",
        "market_hours_mode",
        "expire_at",
        "notify_channel_ids",
    }
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError(f"不支持修改字段: {sorted(unknown)[0]}")

    if "name" in updates:
        name = str(updates["name"] or "").strip()
        if not name:
            raise ValueError("提醒名称不能为空")
        rule.name = name
    if "enabled" in updates:
        rule.enabled = bool(updates["enabled"])
    if "condition_group" in updates:
        rule.condition_group = validate_condition_group(updates["condition_group"])
    if "notify_channel_ids" in updates:
        channel_ids = updates["notify_channel_ids"]
        if not isinstance(channel_ids, list):
            raise ValueError("notify_channel_ids 必须是数组")
        rule.notify_channel_ids = list(channel_ids)
    if "cooldown_minutes" in updates:
        try:
            cooldown = int(updates["cooldown_minutes"])
        except (TypeError, ValueError) as exc:
            raise ValueError("冷却时间必须是非负整数") from exc
        if cooldown < 0:
            raise ValueError("冷却时间必须是非负整数")
        rule.cooldown_minutes = cooldown
    if "max_triggers_per_day" in updates:
        try:
            max_triggers = int(updates["max_triggers_per_day"])
        except (TypeError, ValueError) as exc:
            raise ValueError("每日最大触发次数必须是非负整数") from exc
        if max_triggers < 0:
            raise ValueError("每日最大触发次数必须是非负整数")
        rule.max_triggers_per_day = max_triggers
    if "repeat_mode" in updates:
        repeat_mode = str(updates["repeat_mode"] or "").strip().lower()
        if repeat_mode not in {"once", "repeat"}:
            raise ValueError("repeat_mode 只能是 once 或 repeat")
        rule.repeat_mode = repeat_mode
    if "market_hours_mode" in updates:
        market_hours_mode = str(updates["market_hours_mode"] or "").strip().lower()
        if market_hours_mode not in {"always", "trading_only"}:
            raise ValueError("market_hours_mode 只能是 always 或 trading_only")
        rule.market_hours_mode = market_hours_mode
    if "expire_at" in updates:
        rule.expire_at = parse_expire_at(updates["expire_at"])

    if "direction" in updates or "target_price" in updates:
        direction = str(updates.get("direction") or "").strip().lower()
        if "direction" not in updates:
            items = (rule.condition_group or {}).get("items") or []
            current = next(
                (item for item in items if isinstance(item, dict) and item.get("type") == "price"),
                None,
            )
            current_op = str(current.get("op") or "") if current else ""
            direction = "above" if current_op in {">", ">="} else "below" if current_op in {"<", "<="} else ""
        if direction not in {"above", "below"}:
            raise ValueError("提醒方向只能是 above 或 below")

        if "target_price" in updates:
            try:
                target_price = float(updates["target_price"])
            except (TypeError, ValueError) as exc:
                raise ValueError("提醒价格必须是大于零的数字") from exc
        else:
            items = (rule.condition_group or {}).get("items") or []
            current = next(
                (item for item in items if isinstance(item, dict) and item.get("type") == "price"),
                None,
            )
            try:
                target_price = float(current["value"]) if current else 0
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError("修改方向时必须有现有价格条件") from exc
        if target_price <= 0:
            raise ValueError("提醒价格必须大于零")

        condition_group = dict(rule.condition_group or {})
        items = [dict(item) for item in condition_group.get("items") or [] if isinstance(item, dict)]
        price_item = next((item for item in items if item.get("type") == "price"), None)
        if price_item is None:
            items.insert(0, {"type": "price", "op": ">=", "value": target_price})
            price_item = items[0]
        price_item["op"] = ">=" if direction == "above" else "<="
        price_item["value"] = target_price
        condition_group["items"] = items
        rule.condition_group = validate_condition_group(condition_group)

    rule.trigger_count_today = 0
    rule.trigger_date = ""
    db.commit()
    db.refresh(rule)
    return rule


def delete_alert_rule(db: Session, rule_id: int) -> None:
    """Delete a rule and its hit history explicitly for SQLite compatibility."""
    rule = get_alert_rule(db, rule_id)
    if rule is None:
        raise LookupError("价格提醒不存在")
    db.query(PriceAlertHit).filter(PriceAlertHit.rule_id == rule_id).delete(
        synchronize_session=False
    )
    db.delete(rule)
    db.commit()
