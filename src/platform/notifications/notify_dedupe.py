"""Notification dedupe helpers.

We reuse the existing notify_throttle table to implement idempotency for
batch agents (avoid duplicate notifications on restarts/manual triggers).
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from src.core.timezone import utc_now
from src.web.database import SessionLocal
from src.web.models import NotifyThrottle


def build_notify_dedupe_key(agent_name: str, title: str, content: str) -> str:
    base = "|".join(
        [
            (agent_name or "").strip(),
            (title or "").strip(),
            " ".join((content or "").strip().split())[:1200],
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _now_utc_naive():
    return utc_now().replace(tzinfo=None)


def check_and_mark_notify(
    *,
    agent_name: str,
    scope: str,
    ttl_minutes: int,
    mark: bool,
) -> bool:
    """Check whether a notification should be allowed.

    Args:
        agent_name: agent name.
        scope: a unique scope under the agent (we store in stock_symbol).
        ttl_minutes: within this window we treat as duplicate.
        mark: whether to update last_notify_at when allowed.

    Returns:
        True if allowed; False if deduped.
    """

    if ttl_minutes <= 0:
        return True

    db = SessionLocal()
    try:
        now = _now_utc_naive()
        threshold = now - timedelta(minutes=ttl_minutes)

        record = (
            db.query(NotifyThrottle)
            .filter(
                NotifyThrottle.agent_name == agent_name,
                NotifyThrottle.stock_symbol == scope,
            )
            .first()
        )

        if record and record.last_notify_at and record.last_notify_at >= threshold:
            return False

        if mark:
            if record:
                record.last_notify_at = now
                record.notify_count = (record.notify_count or 0) + 1
            else:
                db.add(
                    NotifyThrottle(
                        agent_name=agent_name,
                        stock_symbol=scope,
                        last_notify_at=now,
                        notify_count=1,
                    )
                )
            db.commit()
        return True
    except Exception:
        db.rollback()
        # If dedupe fails, prefer sending rather than dropping.
        return True
    finally:
        db.close()
