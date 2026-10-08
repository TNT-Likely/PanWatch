"""Read-only monitoring and delivery health, without channel credentials."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func

from src.platform.persistence.models import PriceAlertDelivery, PriceAlertHealth, PriceAlertHit, PriceAlertRule, PriceAlertScanHealth


def iso(value):
    return value.replace(tzinfo=UTC).isoformat() if value else None


def monitoring_health(db, *, rule_ids=None, limit=200):
    now = datetime.now(UTC).replace(tzinfo=None)
    scan = db.get(PriceAlertScanHealth, 1)
    query = db.query(PriceAlertRule).order_by(PriceAlertRule.id)
    if rule_ids is not None:
        query = query.filter(PriceAlertRule.id.in_(rule_ids))
    total = query.count()
    rules = query.limit(limit).all()
    ids = [r.id for r in rules]
    checks = {h.rule_id: h for h in db.query(PriceAlertHealth).filter(PriceAlertHealth.rule_id.in_(ids)).all()}
    counts = {}
    for rule_id, status, count in db.query(PriceAlertHit.rule_id, PriceAlertDelivery.status, func.count(PriceAlertDelivery.id)).join(PriceAlertDelivery, PriceAlertDelivery.hit_id == PriceAlertHit.id).filter(PriceAlertHit.rule_id.in_(ids)).group_by(PriceAlertHit.rule_id, PriceAlertDelivery.status).all():
        counts.setdefault(rule_id, {})[status] = count
    items = []
    for rule in rules:
        check = checks.get(rule.id)
        market_tz = {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}.get(rule.stock.market if rule.stock else "", "UTC")
        today = datetime.now(ZoneInfo(market_tz)).date().isoformat()
        used = (rule.trigger_count_today or 0) if rule.trigger_date == today else 0
        cap = rule.max_triggers_per_day or 0
        status = check.status if check else "never_checked"
        if not rule.enabled:
            status = "once_triggered" if rule.repeat_mode == "once" and rule.last_trigger_at else "disabled"
        elif rule.expire_at and rule.expire_at <= now:
            status = "expired"
        elif cap > 0 and used >= cap:
            status = "daily_limit"
        elif check and check.next_scan_at and now > check.next_scan_at + timedelta(seconds=120):
            status = "monitoring_delayed"
        items.append({"rule_id": rule.id, "name": rule.name, "symbol": rule.stock.symbol if rule.stock else None,
                      "market": rule.stock.market if rule.stock else None, "enabled": bool(rule.enabled), "status": status,
                      "last_check_status": check.status if check else "never_checked",
                      "last_checked_at": iso(check.last_checked_at) if check else None,
                      "last_success_at": iso(check.last_success_at) if check else None,
                      "next_scan_at": iso(check.next_scan_at) if check and rule.enabled else None,
                      "consecutive_failures": check.consecutive_failures if check else 0,
                      "quota": {"kind": "daily_trigger_count", "used": used, "limit": cap, "unlimited": cap == 0, "exhausted": cap > 0 and used >= cap, "day": today},
                      "deliveries": counts.get(rule.id, {})})
    all_counts = dict(db.query(PriceAlertDelivery.status, func.count(PriceAlertDelivery.id)).group_by(PriceAlertDelivery.status).all()) if rule_ids is None else {state: sum(i["deliveries"].get(state, 0) for i in items) for state in {s for i in items for s in i["deliveries"]}}
    scan_status = scan.status if scan else "never_checked"
    if scan and scan.next_scan_at and now > scan.next_scan_at + timedelta(seconds=120):
        scan_status = "monitoring_delayed"
    return {"observed_at": iso(now), "scope": "price_alerts", "total_rules": total, "truncated": total > len(items),
            "scan": {"status": scan_status, "last_started_at": iso(scan.last_started_at) if scan else None,
                     "last_completed_at": iso(scan.last_completed_at) if scan else None, "next_scan_at": iso(scan.next_scan_at) if scan else None},
            "delivery_counts": all_counts, "items": items,
            "delivery_semantics": "at_least_once", "provider_acceptance_is_not_recipient_read": True,
            "ai_spend_budget": "not_enforced_by_price_alerts"}
