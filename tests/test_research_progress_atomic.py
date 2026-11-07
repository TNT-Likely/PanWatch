import asyncio
import threading

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from src.modules.research.orchestration import repository
from src.modules.research.orchestration.models import ResearchRun
from src.platform.persistence.database import Base


@pytest.mark.parametrize('initial_progress', [None, {}])
def test_parallel_role_events_preserve_every_completed_stage(tmp_path, monkeypatch, initial_progress):
    engine = create_engine(f'sqlite:///{tmp_path / "progress.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(repository, 'engine', engine)
    roles = ['technical', 'flow', 'events', 'manager', 'strategy', 'risk']
    barrier = threading.Barrier(len(roles))
    with Session(engine) as db, db.begin():
        db.add(ResearchRun(id='parallel-run', trace_id='parallel-trace', agent_name='premarket_outlook',
            scope_hash='fixture', engine='langgraph', as_of='2026-11-06T01:10:00Z', status='running', progress=initial_progress))

    @event.listens_for(engine, 'after_cursor_execute')
    def synchronize_legacy_reads(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT') and 'research_runs' in statement:
            # A read-modify-write implementation reads the same old document in every worker.
            barrier.wait(timeout=5)

    async def publish():
        await asyncio.gather(*(repository.progress('parallel-run', {'role': role, 'status': 'running'}) for role in roles))
        await asyncio.gather(*(repository.progress('parallel-run', {'role': role, 'status': 'done', 'model': 'fixture'}) for role in roles))
    try:
        asyncio.run(publish())
        event.remove(engine, 'after_cursor_execute', synchronize_legacy_reads)
        with Session(engine) as db:
            stages = db.get(ResearchRun, 'parallel-run').progress
            assert set(stages) == set(roles)
            assert all(stages[role] == {'role': role, 'status': 'done', 'model': 'fixture'} for role in roles)
    finally:
        engine.dispose()
