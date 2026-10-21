"""The frozen research migration must support new and already-populated databases."""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from src.modules.research.orchestration.models import (
    ResearchRun, ResearchArtifact, ResearchDelivery, ResearchOutbox,
)
from src.platform.persistence.migrations import _m138_investment_research


MODELS = (ResearchRun, ResearchArtifact, ResearchDelivery, ResearchOutbox)


def schema(engine, table):
    inspector = inspect(engine)
    return {
        "columns": [(c["name"], str(c["type"]), c["nullable"], c["default"])
                    for c in inspector.get_columns(table)],
        "primary_key": inspector.get_pk_constraint(table)["constrained_columns"],
        "indexes": inspector.get_indexes(table),
    }


@pytest.mark.parametrize("existing_tables", [False, True])
def test_research_migration_matches_orm_and_preserves_records(existing_tables):
    engine = create_engine("sqlite://")
    reference = create_engine("sqlite://")
    try:
        for model in MODELS:
            model.__table__.create(reference)
            if existing_tables:
                model.__table__.create(engine)
        with engine.begin() as conn:
            _m138_investment_research(conn)
        for model in MODELS:
            name = model.__tablename__
            assert schema(engine, name) == schema(reference, name)
        with Session(engine) as db, db.begin():
            db.add_all([
                ResearchRun(id="run", trace_id="trace", agent_name="daily_report",
                            scope_hash="scope", engine="langgraph", as_of="2026-10-21T00:00:00Z",
                            status="ready", payload={"result": "保留"}),
                ResearchArtifact(run_id="run", artifact_id="technical", payload={"claims": [1]}),
                ResearchDelivery(run_id="run", payload_hash="digest", payload={"report": "saved"}, status="sent"),
                ResearchOutbox(run_id="run", channel_key="test", status="sent", policy_scope="scope"),
            ])
        with engine.begin() as conn:
            before = {m.__tablename__: conn.execute(text(f"SELECT * FROM {m.__tablename__}")).all()
                      for m in MODELS}
            _m138_investment_research(conn)
            _m138_investment_research(conn)
            after = {m.__tablename__: conn.execute(text(f"SELECT * FROM {m.__tablename__}")).all()
                     for m in MODELS}
        assert after == before
    finally:
        engine.dispose()
        reference.dispose()
