"""Recommendation meaning must survive parsing, persistence and legacy reads."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.modules.automation import suggestion_pool
from src.modules.automation.agent_prediction_evaluation import group_prediction_outcomes
from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.modules.automation.premarket_outlook import PremarketOutlookAgent
from src.modules.automation.daily_report import DailyReportAgent
from src.modules.automation.tradingagents.operations import _compute_stats
from src.modules.research.signals.actions import normalize_suggestion
from src.platform.persistence.database import Base
from src.platform.persistence.models import StockSuggestion


@pytest.fixture
def pool(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'suggestions.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(suggestion_pool, 'SessionLocal', factory)
    yield factory
    engine.dispose()


@pytest.mark.parametrize(('action', 'label', 'expected'), [
    ('buy', '增持 (置信度 7/10)', 'add'), ('sell', '减持', 'reduce'),
    ('overweight', 'Overweight', 'add'), ('underweight', 'Underweight', 'reduce'),
])
def test_legacy_ta_pool_records_recover_distinct_directions(pool, action, label, expected):
    with pool() as db:
        db.add(StockSuggestion(stock_symbol='601238', stock_market='CN', stock_name='广汽集团',
                               agent_name='tradingagents', action=action, action_label=label))
        db.commit()
    latest = suggestion_pool.get_latest_suggestions(['601238'])['CN:601238']
    assert latest['action'] == expected
    assert latest['status'] == 'ready'


def test_review_replaces_previous_buy_and_survives_persistence(pool):
    common = dict(stock_symbol='601238', stock_name='广汽集团', agent_name='tradingagents')
    assert suggestion_pool.save_suggestion(**common, action='buy', action_label='买入')
    assert suggestion_pool.save_suggestion(**common, action='hold', action_label='待人工复核',
                                          suggestion_state={'rating_raw': 'review', 'review_required': True})
    latest = suggestion_pool.get_latest_suggestions(['601238'])['CN:601238']
    assert latest['action'] == 'watch'
    assert latest['review_required'] is True
    assert latest['rating_raw'] == 'review'
    assert latest['should_alert'] is True
    with pool() as db:
        assert db.query(StockSuggestion).count() == 2


def test_attention_is_separate_from_direction_and_notification_choice_is_kept(pool):
    common = dict(stock_symbol='601238', stock_name='广汽集团', agent_name='intraday_monitor')
    assert suggestion_pool.save_suggestion(**common, action='alert', action_label='设置预警')
    latest = suggestion_pool.get_latest_suggestions(['601238'])['CN:601238']
    assert latest['action'] == 'watch'
    assert latest['attention_required'] is True
    assert latest['should_alert'] is True
    assert suggestion_pool.save_suggestion(**common, action='buy', action_label='准备建仓',
                                          suggestion_state={'should_alert': False})
    latest = suggestion_pool.get_latest_suggestions(['601238'])['CN:601238']
    assert latest['action'] == 'buy'
    assert latest['should_alert'] is False


@pytest.mark.parametrize('agent_cls', [PremarketOutlookAgent, DailyReportAgent])
def test_report_parsers_share_action_labels_and_attention_state(agent_cls):
    watchlist = [SimpleNamespace(symbol='601238', market='CN', name='广汽集团')]
    result = agent_cls()._parse_suggestions_json({'suggestions': [
        {'symbol': '601238', 'action': 'alert', 'action_label': '设置预警'},
    ]}, watchlist)['601238']
    assert result['action'] == 'watch'
    assert result['attention_required'] is True
    assert result['should_alert'] is True


def test_intraday_parser_accepts_attention_without_inventing_an_action():
    result = IntradayMonitorAgent()._parse_suggestion(
        '{"action":"watch","action_label":"观望","attention_required":true,"reason":"风险上升"}'
    )
    assert result['action'] == 'watch'
    assert result['attention_required'] is True
    assert result['should_alert'] is True
    assert normalize_suggestion({'action': 'unknown'})['review_required'] is True


def test_review_never_contributes_to_ta_hit_rates():
    stats = _compute_stats([
        {'action': 'add', 'return_20d_pct': 5, 'hit_20d': True},
        {'action': 'reduce', 'return_20d_pct': -3, 'hit_20d': True},
        {'action': 'watch', 'return_20d_pct': 0, 'hit_20d': True, 'review_required': True},
    ])
    assert stats['buy_count'] == stats['sell_count'] == 1
    assert stats['hold_count'] == 0
    assert stats['overall_hit_rate'] == 1
    assert stats['avg_return_20d_pct'] == 1


@pytest.mark.parametrize('state', [{'review_required': True}, {'attention_required': True}])
def test_attention_only_or_review_predictions_are_not_flat_hits(state):
    row = SimpleNamespace(id=1, agent_name='intraday_monitor', stock_symbol='601238', stock_market='CN',
        prediction_date='2026-10-03', horizon_days=1, prediction_group_id='g', action='watch', action_label='观望',
        outcome_status='evaluated', outcome_return_pct=0.2, meta={'suggestion_state': state})
    result = group_prediction_outcomes([row])
    assert result[0]['outcomes']['1']['hit'] is None
    assert result[0]['review_required'] is state.get('review_required', False)
    assert result[0]['attention_required'] is state.get('attention_required', False)


@pytest.mark.parametrize('state_key', ['attention_required', 'review_required'])
def test_intraday_analysis_records_state_for_prediction_evaluation(monkeypatch, state_key):
    from src.modules.automation import intraday_monitor as module
    from src.modules.automation.tradingagents import operations
    from src.platform.marketdata.models import MarketCode

    agent = IntradayMonitorAgent()
    monkeypatch.setattr(agent, 'build_prompt', lambda *_: ('system', 'prompt'))
    response = '{"action":"watch","' + state_key + '":true,"reason":"风险上升"}'
    context = SimpleNamespace(ai_client=SimpleNamespace(chat=AsyncMock(return_value=response)),
                              report_language='zh-CN', model_label='')
    stock = SimpleNamespace(symbol='601238', name='广汽集团', market=MarketCode.CN,
                            current_price=5.84, change_pct=1.0)
    predictions = Mock(return_value=True)
    monkeypatch.setattr(module, 'save_suggestion', Mock(return_value=True))
    monkeypatch.setattr(module, 'save_agent_context_run', Mock(return_value=True))
    monkeypatch.setattr(module, 'save_agent_prediction_outcome', predictions)
    monkeypatch.setattr(operations, 'try_auto_trigger', Mock())

    result = asyncio.run(agent.analyze(context, {'stock_data': stock, 'timestamp': '2026-10-03'}))

    assert result.raw_data['suggestion'][state_key] is True
    assert predictions.call_count == 2
    for call in predictions.call_args_list:
        assert call.kwargs['meta']['suggestion_state'][state_key] is True
        assert call.kwargs['action'] == 'watch'


def test_backfill_preserves_canonical_direction_and_confidence(pool, monkeypatch):
    from datetime import date
    from src.modules.automation.tradingagents import operations
    from src.platform.persistence.models import AnalysisHistory

    monkeypatch.setattr(operations, 'SessionLocal', pool)
    with pool() as db:
        db.add(AnalysisHistory(agent_name='tradingagents', stock_symbol='601238',
            analysis_date=date.today().isoformat(), content='Historical report', raw_data={
                'suggestion': {'action': 'buy', 'action_label': '增持', 'confidence': 7.5},
            }))
        db.commit()

    result = operations.backfill_tradingagents_suggestions()

    assert result == {'checked': 1, 'written': 1, 'skipped': 0}
    latest = suggestion_pool.get_latest_suggestions(['601238'])['CN:601238']
    assert latest['action'] == 'add'
    assert latest['meta']['confidence'] == 7.5


@pytest.mark.parametrize('agent_cls', [PremarketOutlookAgent, DailyReportAgent])
@pytest.mark.parametrize(('line', 'expected'), [
    ('「601238」减仓：风险上升，避免盲目买入', 'reduce'),
    ('「601238」买入：企稳后轻仓参与，跌破支撑则卖出', 'buy'),
    ('「601238」继续持有：观察是否需要减仓', 'hold'),
])
def test_report_text_fallback_uses_recommendation_before_its_reason(agent_cls, line, expected):
    from src.platform.marketdata.models import MarketCode
    watchlist = [SimpleNamespace(symbol='601238', market=MarketCode.CN, name='广汽集团')]
    result = agent_cls()._parse_suggestions(line, watchlist)
    assert result['601238']['action'] == expected
