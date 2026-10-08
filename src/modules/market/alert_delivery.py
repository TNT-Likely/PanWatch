"""Durable per-channel price-alert delivery with expiring worker leases.

Delivery is at least once: a provider may accept before the process crashes.
The stable event ID is included in content; providers without idempotency can
still receive a duplicate. Credentials are loaded only when sending.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, update

from src.platform.notifications.notifier import NotifierManager
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import NotifyChannel, PriceAlertDelivery, PriceAlertHit


def now_utc():
    return datetime.now(UTC).replace(tzinfo=None)


def enqueue(db, hit, rule, title, content):
    """Called inside the hit + inbox transaction. Never commits or sends."""
    ids = list(dict.fromkeys(rule.notify_channel_ids or []))
    if not ids:
        ids = [c.id for c in db.query(NotifyChannel).filter_by(enabled=True, is_default=True).all()]
    for channel_id in ids or [None]:
        key = str(channel_id) if channel_id is not None else "unconfigured"
        event_id = f"price-alert:{hit.id}:{key}:{uuid4().hex}"
        db.add(PriceAlertDelivery(
            hit_id=hit.id, channel_id=channel_id, channel_key=key,
            event_id=event_id, title=title, content=f"{content}\n\nEvent ID: {event_id}",
            status="pending" if channel_id is not None else "blocked",
            error_code="" if channel_id is not None else "channel_missing",
            next_attempt_at=now_utc() if channel_id is not None else None,
        ))


def serialize(row):
    def iso(value):
        return value.replace(tzinfo=UTC).isoformat() if value else None
    return {"id": row.id, "event_id": row.event_id, "channel_id": row.channel_id,
            "status": row.status, "attempts": row.attempts, "max_attempts": row.max_attempts,
            "next_attempt_at": iso(row.next_attempt_at), "delivered_at": iso(row.delivered_at),
            "error_code": row.error_code}


def update_hit_receipt(db, hit_id):
    rows = db.query(PriceAlertDelivery).filter_by(hit_id=hit_id).all()
    hit = db.get(PriceAlertHit, hit_id)
    if hit is not None and rows:
        hit.notify_success = all(r.status == "delivered" for r in rows)
        # Safe codes only; provider errors can contain tokens, URLs or body data.
        hit.notify_error = "" if hit.notify_success else ",".join(sorted({r.error_code or r.status for r in rows if r.status != "delivered"}))


def retry_delivery(db, delivery_id):
    row = db.get(PriceAlertDelivery, delivery_id)
    if row is None:
        raise LookupError("delivery_not_found")
    if row.status not in {"retry", "failed", "blocked"}:
        raise ValueError("delivery_not_retryable")
    channel = db.get(NotifyChannel, row.channel_id) if row.channel_id is not None else None
    if channel is None or not channel.enabled:
        raise ValueError("delivery_channel_unavailable")
    # Conditional update prevents a manual retry from stealing an active lease.
    changed = db.execute(update(PriceAlertDelivery).where(
        PriceAlertDelivery.id == row.id, PriceAlertDelivery.status.in_(["retry", "failed", "blocked"])
    ).values(status="pending", error_code="", next_attempt_at=now_utc(),
             max_attempts=PriceAlertDelivery.attempts + 5, lease_token="", lease_until=None)).rowcount
    if not changed:
        raise ValueError("delivery_not_retryable")
    db.expire_all()
    update_hit_receipt(db, row.hit_id)
    db.commit()
    return serialize(db.get(PriceAlertDelivery, delivery_id))


class AlertDeliveryWorker:
    def __init__(self, session_factory=None):
        self.session_factory = session_factory or SessionLocal

    def claim(self):
        now = now_utc()
        due = or_(
            and_(PriceAlertDelivery.status.in_(["pending", "retry"]), PriceAlertDelivery.next_attempt_at <= now),
            and_(PriceAlertDelivery.status == "sending", PriceAlertDelivery.lease_until <= now),
        )
        with self.session_factory() as db:
            # Recover even the final expired attempt without sending forever.
            exhausted = db.query(PriceAlertDelivery).filter(due, PriceAlertDelivery.attempts >= PriceAlertDelivery.max_attempts).all()
            for row in exhausted:
                changed = db.execute(update(PriceAlertDelivery).where(
                    PriceAlertDelivery.id == row.id, due,
                    PriceAlertDelivery.attempts >= PriceAlertDelivery.max_attempts,
                ).values(status="failed", error_code="attempts_exhausted", lease_token="", lease_until=None, next_attempt_at=None)).rowcount
                if changed:
                    db.expire_all()
                    update_hit_receipt(db, row.hit_id)
            db.commit()
            row = db.query(PriceAlertDelivery).filter(due, PriceAlertDelivery.attempts < PriceAlertDelivery.max_attempts).order_by(PriceAlertDelivery.id).first()
            if row is None:
                return None
            token = uuid4().hex
            changed = db.execute(update(PriceAlertDelivery).where(PriceAlertDelivery.id == row.id, due).values(
                status="sending", lease_token=token, lease_until=now + timedelta(seconds=90),
                attempts=PriceAlertDelivery.attempts + 1,
            )).rowcount
            if not changed:
                db.rollback()
                return None
            db.commit()
            db.refresh(row)
            channel = db.get(NotifyChannel, row.channel_id) if row.channel_id is not None else None
            return {"id": row.id, "hit_id": row.hit_id, "token": token,
                    "title": row.title, "content": row.content,
                    "channel_type": channel.type if channel else None,
                    "config": dict(channel.config or {}) if channel else {},
                    "available": bool(channel and channel.enabled and db.get(PriceAlertHit, row.hit_id))}

    def finish(self, claim, *, ok=False, error=""):
        with self.session_factory() as db:
            row = db.get(PriceAlertDelivery, claim["id"])
            if row is None or row.status != "sending" or row.lease_token != claim["token"]:
                return
            now = now_utc()
            status = "delivered" if ok else "blocked" if error == "channel_unavailable" else "failed" if row.attempts >= row.max_attempts else "retry"
            next_at = now + timedelta(seconds=min(1800, 30 * 2 ** min(row.attempts - 1, 6))) if status == "retry" else None
            changed = db.execute(update(PriceAlertDelivery).where(
                PriceAlertDelivery.id == row.id, PriceAlertDelivery.status == "sending",
                PriceAlertDelivery.lease_token == claim["token"],
            ).values(status=status, error_code=error, delivered_at=now if ok else None,
                     next_attempt_at=next_at, lease_token="", lease_until=None)).rowcount
            if changed:
                db.expire_all()
                update_hit_receipt(db, claim["hit_id"])
            db.commit()

    async def drain(self, limit=5):
        processed = 0
        for _ in range(limit):
            claim = await asyncio.to_thread(self.claim)
            if claim is None:
                break
            if not claim["available"]:
                self.finish(claim, error="channel_unavailable")
            else:
                manager = NotifierManager()
                manager.add_channel(claim["channel_type"], claim["config"])
                try:
                    result = await asyncio.wait_for(manager.notify_with_result(claim["title"], claim["content"]), timeout=45)
                    self.finish(claim, ok=bool(result.get("success")), error="" if result.get("success") else "channel_send_failed")
                except TimeoutError:
                    self.finish(claim, error="delivery_timeout")
                except Exception:
                    self.finish(claim, error="channel_send_failed")
                # Cancellation/process death leaves the lease for recovery.
            processed += 1
        return {"processed": processed}
