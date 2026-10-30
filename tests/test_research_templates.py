import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from test_investment_research_integration import host, configure, make_context, collected, scripts
from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.modules.research.orchestration import service
from src.modules.research.orchestration.policies import validate_config, templates_for
from src.modules.research.orchestration.models import ResearchRun, ResearchDelivery, ResearchArtifact, ResearchOutbox
from src.platform.persistence.models import AnalysisHistory, StockSuggestion, AgentPredictionOutcome


def fast_config(engine, **kwargs):
    configure(engine, 'intraday_monitor', template='intraday_fast', specialists=['technical'], max_feedback=0, **kwargs)


async def changed(context):
    data = await collected(context)
    data['signal_pack'].quote.change_pct = 7
    data['signal_pack'].technical['atr_pct'] = 2
    data['symbol_contexts'] = {s.symbol: {'relative_strength': {'excess_return_5d': 2},
        'ta_verdict': {'rating': 'BUY', 'days_ago': 1}, 'events': []} for s in context.watchlist}
    return data


def test_no_change_does_not_replace_history_predictions_or_call_context_enrichment(host, monkeypatch, tmp_path):
    engine, model = host
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    fast_config(engine)
    monkeypatch.setattr(IntradayMonitorAgent, 'collect', lambda self, context: collected(context))
    enrichment = AsyncMock(side_effect=AssertionError('Quiet run must not enrich historical context'))
    monkeypatch.setattr('src.modules.research.context_builder.ContextBuilder.build_symbol_contexts', enrichment)
    result = asyncio.run(IntradayMonitorAgent().run(make_context('quiet')))
    assert result.raw_data['completion'] == 'skipped' and result.raw_data['should_alert'] is False
    assert result.raw_data['suggestions'] == {} and model.calls == {}
    enrichment.assert_not_awaited()
    with Session(engine) as db:
        assert db.scalars(select(ResearchRun)).one().payload['usage']['attempts'] == 0
        assert db.scalars(select(ResearchDelivery)).one().status == 'skipped'
        for cls in [AnalysisHistory, StockSuggestion, AgentPredictionOutcome, ResearchOutbox]:
            assert not db.scalars(select(cls)).all()


def test_changed_then_repeated_snapshot_keeps_last_valid_advice_and_skips_second_run(host, monkeypatch, tmp_path):
    engine, model = host
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    fast_config(engine)
    monkeypatch.setattr(IntradayMonitorAgent, 'collect', lambda self, context: changed(context))
    first = asyncio.run(IntradayMonitorAgent().run(make_context('changed')))
    assert first.raw_data['completion'] == 'complete', first.content
    assert first.raw_data['usage']['attempts'] == 2
    assert {a['role'] for a in first.raw_data['research_report']['process']} == {'gate', 'technical', 'strategy', 'execution'}
    assert '技术面：' in first.content and 'technical finding' in first.content
    before = dict(model.calls)
    second = asyncio.run(IntradayMonitorAgent().run(make_context('repeated')))
    assert second.raw_data['completion'] == 'skipped' and dict(model.calls) == before
    with Session(engine) as db:
        assert len(db.scalars(select(AnalysisHistory)).all()) == len(db.scalars(select(StockSuggestion)).all()) == 1
        assert len(db.scalars(select(AgentPredictionOutcome)).all()) == 2
        assert len(db.scalars(select(ResearchRun)).all()) == 2
    technical = next(p for role, p in model.payloads if role == 'technical')['input']
    content = next(e['content'] for e in technical['evidence'] if e['domain'] == 'technical')
    assert content['analysis_context']['relative_strength']['excess_return_5d'] == 2
    assert content['analysis_context']['ta_verdict']['days_ago'] == 1
    assert content['analysis_context']['volatility_rules']['adaptive_price_threshold'] == 3
    assert technical['portfolio'] == {}


def test_failed_signal_is_retryable_and_does_not_publish_a_draft(host, monkeypatch, tmp_path):
    engine, _ = host
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    fast_config(engine, max_attempts=1)
    monkeypatch.setattr(IntradayMonitorAgent, 'collect', lambda self, context: changed(context))
    first = asyncio.run(IntradayMonitorAgent().run(make_context('failure')))
    assert first.raw_data['completion'] == 'budget_exhausted' and first.raw_data['suggestions'] == {}
    with Session(engine) as db, db.begin():
        from src.platform.persistence.models import AgentConfig
        row = db.scalars(select(AgentConfig).where(AgentConfig.name == 'intraday_monitor')).one()
        config = dict(row.config);config['research_graph'] = {**config['research_graph'], 'max_attempts': 6};row.config = config
    second = asyncio.run(IntradayMonitorAgent().run(make_context('retry')))
    assert second.raw_data['completion'] == 'complete', second.content
    with Session(engine) as db:
        assert len(db.scalars(select(StockSuggestion)).all()) == 1


def test_templates_validate_dependencies_and_preserve_explicit_global_budget_caps():
    for name in ['daily_report', 'premarket_outlook', 'intraday_monitor']:
        metadata = templates_for(name)
        for template in metadata['templates']:
            _, policy = validate_config({'template': template['id'], **template['defaults']}, name)
            assert policy.graph == template['graph']
    for bad in [{'template': 'unknown'}, {'template': 'intraday_fast', 'debate': True}, {'template': 'intraday_fast', 'max_feedback': 1}, {'template': 'intraday_fast', 'specialists': ['flow']}, {'nodes': ['decision']}, {'edges': []}]:
        with pytest.raises((ValueError, TypeError)):
            validate_config(bad, 'intraday_monitor')
    _, policy = validate_config({'max_attempts': 5, 'max_tools': 1}, 'daily_report')
    result = service.scope_policy(policy, {'max_attempts': 5, 'max_tools': 1}, 30)
    assert result.max_attempts == 5 and result.max_tools == 1


def test_session_deadline_defaults_leave_headroom_for_slow_providers():
    assert validate_config({}, 'premarket_outlook')[1].deadline_seconds == 480
    assert validate_config({}, 'daily_report')[1].deadline_seconds == 600
    assert validate_config({}, 'intraday_monitor')[1].deadline_seconds == 120


def test_explicit_review_runs_without_price_trigger_and_baseline_failure_preserves_audit(host, monkeypatch, tmp_path):
    engine, _ = host
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    fast_config(engine, trigger_mode='always')
    monkeypatch.setattr(IntradayMonitorAgent, 'collect', lambda self, context: collected(context))
    def unavailable(_):
        raise OSError('Controlled baseline write failure')
    monkeypatch.setattr('src.modules.research.orchestration.signals.acknowledge', unavailable)
    result = asyncio.run(IntradayMonitorAgent().run(make_context('manual-review')))
    assert result.raw_data['completion'] == 'complete', result.content
    assert result.raw_data['usage']['attempts'] == 2
    with Session(engine) as db:
        assert db.scalars(select(ResearchRun)).one().status == 'completed'
        assert len(db.scalars(select(AnalysisHistory)).all()) == 1


def test_template_api_and_invalid_update_preserve_stored_config(host):
    from src.modules.automation.api.agents import router
    from src.platform.persistence.database import get_db
    from src.platform.persistence.models import AgentConfig
    engine, _ = host
    fast_config(engine)
    app = FastAPI();app.include_router(router, prefix="/agents")
    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = database
    client = TestClient(app)
    response = client.get('/agents/research-templates?agent_name=intraday_monitor')
    assert response.status_code == 200 and response.json()['default_template'] == 'intraday_fast'
    bad = client.put('/agents/intraday_monitor', json={'config': {'research_graph': {'template': 'intraday_fast', 'debate': True}}})
    assert bad.status_code == 400
    with Session(engine) as db:
        row = db.scalars(select(AgentConfig).where(AgentConfig.name == 'intraday_monitor')).one()
        assert row.config['research_graph']['template'] == 'intraday_fast' and not row.config['research_graph'].get('debate', False)


def test_active_stock_captures_market_context_but_quiet_run_does_not(host, monkeypatch, tmp_path):
    from src.modules.automation.daily_report import DailyReportAgent
    engine, _ = host
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    fast_config(engine)
    indices = AsyncMock(return_value=[{'name': 'Controlled index', 'symbol': '000001', 'market': 'CN', 'current_price': 3000, 'change_pct': 1}])
    monkeypatch.setattr(DailyReportAgent, '_fetch_index_for_market', indices)
    monkeypatch.setattr(IntradayMonitorAgent, 'collect', lambda self, context: changed(context))
    result = asyncio.run(IntradayMonitorAgent().run(make_context('market-active')))
    assert 'Controlled index' in result.content and result.raw_data['completion'] == 'complete'
    indices.assert_awaited_once()
    result = asyncio.run(IntradayMonitorAgent().run(make_context('market-quiet')))
    assert result.raw_data['completion'] == 'skipped'
    indices.assert_awaited_once()
