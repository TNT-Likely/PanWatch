"""Progress reflects validated research roles, not just successful HTTP/tool calls."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.modules.automation.api.agents import get_run_progress
from src.modules.research.orchestration.models import ResearchRun
from src.platform.persistence.database import Base
from src.platform.persistence.models import AgentRun


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_research(db, *, progress, status="running", payload=None):
    now = datetime.now(timezone.utc)
    db.add(ResearchRun(id="research", trace_id="progress-trace", agent_name="premarket_outlook",
        scope_hash="fixture", engine="langgraph", as_of=now.isoformat(), created_at=now,
        finished_at=now if status != "running" else None, status=status, progress=progress, payload=payload or {}))
    db.commit()


@pytest.mark.parametrize("event_status", ["running", "response_received", "retrying", "tool_started", "tool_done", "tool_rejected"])
def test_unvalidated_role_events_stay_active(db, event_status):
    add_research(db, progress={"technical": {"status": event_status}})

    result = get_run_progress("progress-trace", db)

    assert result["status"] == "running"
    assert result["current_stage"] == "technical"
    assert result["stages"] == [{"name": "technical", "status": "running"}]
    assert result["completed_stages"] == []


def test_only_completed_roles_advance_and_failed_review_are_preserved(db):
    add_research(db, status="completed", progress={
        "gate": {"status": "skipped"}, "technical": {"status": "done"},
        "manager": {"status": "failed"}, "run": {"status": "review"},
    }, payload={"completion": "validation_failed", "status": "review", "reason": "Invalid manager report"})
    db.add(AgentRun(agent_name="premarket_outlook", trace_id="progress-trace", status="success", result="Draft"))
    db.commit()

    result = get_run_progress("progress-trace", db)

    assert result["status"] == result["run"]["status"] == "success"  # execution ended, not research success
    assert result["completed_stages"] == ["gate", "technical"]
    assert result["stages"][-2:] == [{"name": "manager", "status": "failed"}, {"name": "run", "status": "review"}]
    assert result["completion"] == "validation_failed"
    assert result["result_status"] == "review"
    assert result["research_reason"] == "Invalid manager report"


def test_valid_complete_research_can_require_human_review(db):
    add_research(db, status="completed", progress={"decision": {"status": "done"}},
        payload={"completion": "complete", "status": "review", "reason": ""})

    result = get_run_progress("progress-trace", db)

    assert result["status"] == "success"
    assert result["completion"] == "complete"
    assert result["result_status"] == "review"
    assert result["research_reason"] == ""
    assert result["completed_stages"] == ["decision"]
