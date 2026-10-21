from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.modules.research.orchestration.signals import inspect_changes, acknowledge


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    now = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)
    pack = SimpleNamespace(quote=SimpleNamespace(current_price=100, change_pct=1),
        technical={'atr_pct': 4, 'volume_ratio': 1, 'trend': 'up', 'support': 95},
        news=SimpleNamespace(items=[]), events=SimpleNamespace(items=[]))
    return now, pack, IntradayMonitorAgent()


def test_atr_baseline_repeated_threshold_and_meaningful_new_move(setup):
    now, pack, agent = setup
    pack.quote.change_pct = 4
    signals, observation, rules = inspect_changes('CN:600519', pack, [], now, agent)
    assert signals == [] and rules['adaptive_price_threshold'] == 6
    acknowledge({'CN:600519': observation})
    pack.quote.change_pct = 6.5
    signals, observation, _ = inspect_changes('CN:600519', pack, [], now, agent)
    assert signals[0].reasons == ['atr_price_change']
    acknowledge({'CN:600519': observation})
    assert inspect_changes('CN:600519', pack, [], now, agent)[0] == []
    pack.quote.change_pct = 10
    assert 'atr_price_change' in inspect_changes('CN:600519', pack, [], now, agent)[0][0].reasons


def test_failed_research_does_not_consume_signal_and_market_keys_are_isolated(setup):
    now, pack, agent = setup
    pack.quote.change_pct = 7
    first = inspect_changes('CN:600519', pack, [], now, agent)
    assert first[0] and inspect_changes('CN:600519', pack, [], now, agent)[0]
    acknowledge({'CN:600519': first[1]})
    assert not inspect_changes('CN:600519', pack, [], now, agent)[0]
    assert inspect_changes('HK:600519', pack, [], now, agent)[0]


def test_news_dedup_future_cutoff_and_materiality(setup):
    now, pack, agent = setup
    pack.news.items = [{'title': 'New quarterly report', 'time': (now - timedelta(minutes=1)).isoformat(), 'importance': 2},
                       {'title': 'Future announcement', 'time': (now + timedelta(minutes=1)).isoformat()}]
    signals, observation, _ = inspect_changes('CN:600519', pack, [], now, agent)
    assert len(signals) == 1 and signals[0].domain == 'events' and signals[0].material
    acknowledge({'CN:600519': observation})
    assert not inspect_changes('CN:600519', pack, [], now, agent)[0]
    assert inspect_changes('CN:600519', pack, [], now + timedelta(minutes=2), agent)[0]


def test_position_warning_support_break_and_technical_state_changes(setup):
    now, pack, agent = setup
    baseline = inspect_changes('CN:600519', pack, [], now, agent)
    acknowledge({'CN:600519': baseline[1]})
    pack.quote.current_price = 90
    pack.technical['trend'] = 'down'
    positions = [SimpleNamespace(account_id=1, cost_price=100)]
    signals, observation, _ = inspect_changes('CN:600519', pack, positions, now, agent)
    assert {'position_risk_changed', 'support_broken', 'technical_state_changed'} <= set(signals[0].reasons)
    acknowledge({'CN:600519': observation})
    assert not inspect_changes('CN:600519', pack, positions, now, agent)[0]


def test_long_term_position_uses_trading_style_threshold_floor(setup):
    now, pack, agent = setup
    pack.technical['atr_pct'] = 1
    pack.quote.change_pct = 4
    positions = [SimpleNamespace(account_id=1, cost_price=100, trading_style='long')]
    signals, _, rules = inspect_changes('CN:600519', pack, positions, now, agent)
    assert rules['adaptive_price_threshold'] == 5
    assert not signals
