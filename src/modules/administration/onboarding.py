"""Instance-level setup progress backed by verified actions, never browser flags."""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.platform.persistence.models import AIModel, AIService, AppSettings, NotifyChannel

PREFIX = "onboarding."
STATE_KEY = PREFIX + "progress"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_value(db: Session, key: str) -> dict:
    row = db.query(AppSettings).filter(AppSettings.key == key).first()
    try:
        value = json.loads(row.value) if row else {}
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def write_value(db: Session, key: str, value: dict) -> None:
    row = db.query(AppSettings).filter(AppSettings.key == key).first()
    if row is None:
        row = AppSettings(key=key, description="Verified getting-started progress")
        db.add(row)
    row.value = json.dumps(value, ensure_ascii=False)


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def model_fingerprint(model: AIModel, service: AIService) -> str:
    return fingerprint({"model": model.model, "service_id": service.id,
                        "base_url": service.base_url, "api_key": service.api_key})


def channel_fingerprint(channel: NotifyChannel) -> str:
    return fingerprint({"type": channel.type, "config": channel.config or {}})


def record_check(db: Session, kind: str, item_id: int, identity: str, ok: bool) -> None:
    write_value(db, f"{PREFIX}{kind}.{item_id}",
                {"fingerprint": identity, "ok": ok, "tested_at": now_iso()})
    db.commit()


def verified(db: Session, kind: str, item_id: int, identity: str) -> bool:
    check = read_value(db, f"{PREFIX}{kind}.{item_id}")
    return check.get("ok") is True and check.get("fingerprint") == identity


def recover_interrupted_analysis(db: Session) -> None:
    """A worker cannot survive a process restart; make its saved task retryable."""
    from src.platform.persistence.models import AgentRun

    db.query(AgentRun).filter(AgentRun.agent_name == "first_analysis", AgentRun.status == "running").update(
        {"status": "failed", "error": "onboarding_analysis_interrupted"}, synchronize_session=False,
    )
    db.commit()
