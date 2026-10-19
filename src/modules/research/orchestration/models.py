"""Research audit and delivery records; existing business tables remain compatible."""
from sqlalchemy import Column, DateTime, Integer, JSON, String, func

from src.platform.persistence.database import Base


class ResearchRun(Base):
    __tablename__ = "research_runs"
    id = Column(String, primary_key=True)
    trace_id = Column(String, index=True, nullable=False)
    agent_name = Column(String, nullable=False)
    scope_hash = Column(String, nullable=False)
    engine = Column(String, nullable=False)
    as_of = Column(String, nullable=False)
    status = Column(String, nullable=False, default="running")
    payload = Column(JSON, default=dict)
    progress = Column(JSON, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)


class ResearchArtifact(Base):
    __tablename__ = "research_artifacts"
    run_id = Column(String, primary_key=True)
    artifact_id = Column(String, primary_key=True)
    payload = Column(JSON, nullable=False)


class ResearchDelivery(Base):
    __tablename__ = "research_deliveries"
    run_id = Column(String, primary_key=True)
    payload_hash = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String, nullable=False)


class ResearchOutbox(Base):
    __tablename__ = "research_notification_outbox"
    run_id = Column(String, primary_key=True)
    channel_key = Column(String, primary_key=True)
    status = Column(String, nullable=False, default="pending")
    policy_scope = Column(String, nullable=False)
    ttl_minutes = Column(Integer, nullable=False, default=0)
    error = Column(String, default="")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
