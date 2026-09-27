"""Resolve the persisted interface language for generated content."""

from sqlalchemy.orm import Session

from src.platform.persistence.models import AppSettings

SUPPORTED_LANGUAGES = frozenset({"zh-CN", "en-US"})


def _setting_value(db: Session, key: str) -> str:
    row = db.query(AppSettings).filter(AppSettings.key == key).first()
    return str(row.value or "").strip() if row else ""


def resolve_report_language(db: Session) -> str:
    """Use the persisted interface locale for generated prose and background jobs."""
    interface_language = _setting_value(db, "ui_language")
    return interface_language if interface_language in SUPPORTED_LANGUAGES else "zh-CN"
