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


def test_retire_legacy_and_shadow_without_changing_other_settings_or_history(tmp_path):
    import json
    from src.platform.persistence.migrations import _m139_retire_session_legacy
    engine = create_engine(f"sqlite:///{tmp_path / 'retirement.db'}")
    with engine.begin() as db:
        db.execute(text('CREATE TABLE agent_configs (id INTEGER PRIMARY KEY, name TEXT, config JSON)'))
        db.execute(text('CREATE TABLE analysis_history (id INTEGER PRIMARY KEY, content TEXT)'))
        db.execute(text("INSERT INTO analysis_history VALUES (1, 'Historical report')"))
        for index, (name, mode) in enumerate([('daily_report', 'legacy'), ('premarket_outlook', 'shadow'), ('intraday_monitor', 'langgraph'), ('tradingagents', 'legacy')], 1):
            config = {'unrelated': 'preserved', 'research_graph': {'engine': mode, 'max_attempts': 22, 'deadline_seconds': 240}}
            db.execute(text('INSERT INTO agent_configs VALUES (:id,:name,:config)'), {'id': index, 'name': name, 'config': json.dumps(config)})
        _m139_retire_session_legacy(db)
        once = db.execute(text('SELECT config FROM agent_configs ORDER BY id')).fetchall()
        _m139_retire_session_legacy(db)
        assert once == db.execute(text('SELECT config FROM agent_configs ORDER BY id')).fetchall()
        for index, (value,) in enumerate(once):
            config = json.loads(value)
            assert config['unrelated'] == 'preserved' and config['research_graph']['max_attempts'] == 22
            assert config['research_graph']['engine'] == ('legacy' if index == 3 else 'langgraph')
        assert db.execute(text('SELECT content FROM analysis_history')).scalar_one() == 'Historical report'
    engine.dispose()
