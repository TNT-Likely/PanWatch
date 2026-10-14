"""Platform-owned immutable research artifacts and durable task/control state.

Records have explicit kinds and validated contracts at the service boundary.
Historical artifacts are never updated when a champion changes.
"""

from datetime import datetime, timezone
from sqlalchemy import JSON, Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.sql import func

from src.platform.persistence.database import Base


class QlibRecord(Base):
    __tablename__ = "qlib_records"
    __table_args__ = (Index("ix_qlib_record_kind_created", "kind", "created_at"),)
    id = Column(String, primary_key=True)
    kind = Column(String, nullable=False)
    parent_id = Column(String, nullable=True, index=True)
    name = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now())


class QlibJob(Base):
    __tablename__ = "qlib_jobs"
    id = Column(String, primary_key=True)
    request_key = Column(String, nullable=False, unique=True)
    kind = Column(String, nullable=False)
    dataset_id = Column(String, nullable=False)
    model_id = Column(String, nullable=True)
    config = Column(JSON, nullable=False)
    status = Column(String, nullable=False, default="queued", index=True)
    result_id = Column(String, nullable=True)
    error_code = Column(String, nullable=False, default="")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class QlibControl(Base):
    __tablename__ = "qlib_control"
    id = Column(Integer, primary_key=True)
    champion_id = Column(String, nullable=True)
    settings = Column(JSON, nullable=False, default=dict)
    revision = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, server_default=func.now())


class QlibSyncJob(Base):
    """Atomic whole-pool marketdata ingestion, separate from model workers."""
    __tablename__ = "qlib_sync_jobs"
    id = Column(String, primary_key=True)
    request_key = Column(String, nullable=False, unique=True)
    config = Column(JSON, nullable=False)
    status = Column(String, nullable=False, default="queued", index=True)
    progress = Column(JSON, nullable=False, default=dict)
    dataset_id = Column(String, nullable=True)
    train_job_id = Column(String, nullable=True)
    predict_job_id = Column(String, nullable=True)
    error_code = Column(String, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)


class QlibAudit(Base):
    __tablename__ = "qlib_audit"
    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    target_id = Column(String, nullable=True)
    detail = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class QlibTemplate(Base):
    __tablename__ = "qlib_templates"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    config = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class QlibExecution(Base):
    """One execution intent per decision/instrument/side; never retry a filled intent."""
    __tablename__ = "qlib_executions"
    id = Column(String, primary_key=True)
    decision_id = Column(String, nullable=False, index=True)
    run_id = Column(String, nullable=False)
    model_id = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    quantity = Column(Integer, nullable=False, default=0)
    price = Column(Float, nullable=True)
    reason = Column(Text, nullable=False, default="")
    position_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
