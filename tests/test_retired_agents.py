"""Retired agents cannot run or return through an old config backup."""

import asyncio
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.modules.automation.api.templates import (
    TemplateAgent,
    TemplatePayload,
    TemplateStock,
    TemplateStockAgent,
    import_template,
)
from src.modules.portfolio.api.history import list_history
from src.platform.persistence.database import Base
from src.platform.persistence.migrations import _m135_retire_unused_agents
from src.platform.persistence.models import AgentConfig, AnalysisHistory, Stock, StockAgent, StockSuggestion


@pytest.mark.parametrize('retired', ['chart_analyst', 'news_digest'])
def test_retirement_removes_configuration_without_losing_reports_or_reseeding(tmp_path, monkeypatch, retired):
    import server

    engine = create_engine(f"sqlite:///{tmp_path / 'retired-agent.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(server, 'SessionLocal', factory)
    with factory() as db:
        stock = Stock(symbol='601238', name='广汽集团', market='CN')
        db.add(stock)
        db.flush()
        db.add_all([
            AgentConfig(name=retired, display_name='旧分析', enabled=True),
            AgentConfig(name='daily_report', display_name='收盘复盘', enabled=False),
            StockAgent(stock_id=stock.id, agent_name=retired),
            StockAgent(stock_id=stock.id, agent_name='daily_report'),
            AnalysisHistory(agent_name=retired, stock_symbol='601238',
                            analysis_date='2026-09-30', content='旧报告', agent_kind_snapshot=''),
        ])
        db.commit()

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO notify_throttle (agent_name, stock_symbol, last_notify_at) VALUES (:agent, '601238', '2026-09-30 00:00:00')"), {'agent': retired})
        _m135_retire_unused_agents(conn)
        _m135_retire_unused_agents(conn)
        assert conn.execute(text("SELECT COUNT(*) FROM notify_throttle WHERE agent_name = :agent"), {'agent': retired}).scalar() == 0

    server.seed_agents()
    with factory() as db:
        assert db.query(AgentConfig).filter_by(name=retired).first() is None
        assert db.query(AgentConfig).filter_by(name='daily_report').one().enabled is False
        assert [row.agent_name for row in db.query(StockAgent).all()] == ['daily_report']
        legacy = list_history(kind='capability', limit=30, db=db)
        assert len(legacy) == 1 and legacy[0].content == '旧报告'
        assert list_history(kind='workflow', limit=30, db=db) == []
    with pytest.raises(ValueError, match='未注册实际实现'):
        asyncio.run(server.trigger_agent(retired))
    engine.dispose()


@pytest.mark.parametrize('retired', ['chart_analyst', 'news_digest'])
def test_retired_suggestions_do_not_override_current_agent_results(tmp_path, monkeypatch, retired):
    from src.modules.automation import suggestion_pool
    from src.modules.strategy import entry_candidates

    engine = create_engine(f"sqlite:///{tmp_path / 'suggestions.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(suggestion_pool, 'SessionLocal', factory)
    monkeypatch.setattr(entry_candidates, 'SessionLocal', factory)
    with factory() as db:
        for agent in ['daily_report', retired]:
            db.add(StockSuggestion(stock_symbol='601238', stock_name='广汽集团', stock_market='CN',
                                   agent_name=agent, action='watch', action_label='观望'))
            db.commit()
    assert suggestion_pool.get_latest_suggestions(['601238'])['CN:601238']['agent_name'] == 'daily_report'
    assert [item['agent_name'] for item in suggestion_pool.get_suggestions_for_stock('601238')] == ['daily_report']
    assert [item.agent_name for item in entry_candidates._load_latest_suggestions()] == ['daily_report']
    # The explicit historical view still includes the retired agent's record.
    assert len(suggestion_pool.get_suggestions_for_stock('601238', include_expired=True)) == 2
    engine.dispose()


@pytest.mark.parametrize('mode', ['merge', 'replace'])
@pytest.mark.parametrize('retired', ['chart_analyst', 'news_digest'])
def test_legacy_backup_skips_retired_agent_and_stock_binding(tmp_path, monkeypatch, mode, retired):
    monkeypatch.setitem(sys.modules, 'server', SimpleNamespace(reload_scheduler=lambda: False))
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-backup.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        payload = TemplatePayload(
            agents=[TemplateAgent(name=retired, enabled=True), TemplateAgent(name='daily_report')],
            stocks=[TemplateStock(symbol='601238', name='广汽集团', market='CN', agents=[
                TemplateStockAgent(agent_name=retired), TemplateStockAgent(agent_name='daily_report'),
            ])],
        )
        result = import_template(payload=payload, mode=mode, modules='agents,watchlist', db=db)
        assert [row.name for row in db.query(AgentConfig).all()] == ['daily_report']
        assert [row.agent_name for row in db.query(StockAgent).all()] == ['daily_report']
        assert [warning['code'] for warning in result['warnings']] == ['retired_agent', 'retired_agent']
    engine.dispose()
