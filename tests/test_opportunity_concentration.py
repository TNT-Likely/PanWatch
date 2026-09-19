from copy import deepcopy
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.platform.persistence.database import Base
from src.platform.persistence.models import Account, Position, Stock, EntryCandidate
from src.modules.portfolio import holdings, opportunity_risk as risk
from src.modules.portfolio.portfolio_diagnostics import diagnose_positions


@pytest.fixture
def sessions(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(risk, 'SessionLocal', factory)
    monkeypatch.setattr(risk, '_cache', None)
    yield factory
    engine.dispose()


@pytest.mark.parametrize('cn,us,expected', [(100,0,True), (70,30,True), (69,31,False), (0,0,False)])
def test_threshold_and_other_market_unchanged(cn, us, expected):
    items = [{'stock_market':'CN','score':100,'action':'buy'}, {'stock_market':'US','score':90,'action':'buy'}]
    original = deepcopy(items)
    diag = diagnose_positions([{'market':'CN','market_value':cn}, {'market':'US','market_value':us}])
    result = risk.apply_concentration(items, diag)
    assert result[0]['concentration_flag'] is expected
    assert result[0]['score'] == (70 if expected else 100)
    assert result[0]['raw_score'] == 100
    assert result[1]['score'] == 90
    assert items == original and len(result) == len(items)


def test_rank_score_is_adjusted_once_and_exit_signals_are_unchanged():
    diag = {'total_market_value':100, 'by_market':{'CN':100}}
    items = [{'stock_market':'CN','score':90,'rank_score':100,'action':'add'},
             {'stock_market':'CN','score':90,'rank_score':100,'action':'sell'}]
    once = risk.apply_concentration(items, diag)
    assert once[0]['score'] == 63 and once[0]['rank_score'] == 70
    assert once[1]['score'] == 90 and not once[1]['concentration_flag']
    assert risk.apply_concentration(once, diag) == once


def add_position(db, account, stock, quantity=10, cost=5):
    db.add_all([account, stock])
    db.flush()
    pos = Position(account_id=account.id, stock_id=stock.id, quantity=quantity, cost_price=cost)
    db.add(pos)
    db.commit()
    return pos


def test_holdings_merges_enabled_accounts_and_converts_fx(sessions, monkeypatch):
    with sessions() as db:
        stock = Stock(symbol='TEST',name='test',market='US')
        add_position(db, Account(name='one'), stock)
        add_position(db, Account(name='two'), stock, quantity=20)
        add_position(db, Account(name='disabled',enabled=False), stock, quantity=999)
        monkeypatch.setattr(holdings, '_fetch_quotes_for_stocks', lambda _: {'TEST':{'current_price':8}})
        monkeypatch.setattr(holdings, 'get_usd_cny_rate', lambda: 7)
        monkeypatch.setattr(holdings, 'get_hkd_cny_rate', lambda: pytest.fail('no HK positions'))
        result = holdings.gather_holdings(db)
        assert len(result) == 1
        assert result[0]['quantity'] == 30
        assert result[0]['market_value'] == 1680
        assert result[0]['unrealized_pnl'] == 630


def test_cache_invalidates_on_position_cost_enable_and_ttl(sessions, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(risk, 'monotonic', lambda: clock[0])
    value = Mock(return_value=[{'market':'CN','market_value':100}])
    monkeypatch.setattr(risk, 'gather_holdings', value)
    with sessions() as db:
        account = Account(name='test')
        pos = add_position(db, account, Stock(symbol='TEST', name='test', market='CN'))
        risk.current_concentration(); risk.current_concentration()
        assert value.call_count == 1
        pos.quantity = 20; db.commit(); risk.current_concentration()
        pos.cost_price = 6; db.commit(); risk.current_concentration()
        account.enabled = False; db.commit(); risk.current_concentration()
        assert value.call_count == 4
        clock[0] += 31
        risk.current_concentration()
        assert value.call_count == 5


def test_diagnostic_failure_preserves_candidates(monkeypatch):
    monkeypatch.setattr(risk, 'current_concentration', Mock(side_effect=RuntimeError('offline')))
    result = risk.risk_adjusted_opportunities([{'score':100}])
    assert result[0]['score'] == 100
    assert '暂不可用' in result[0]['concentration_note']


def test_candidate_list_adjusts_before_limit_without_persisting(sessions, monkeypatch):
    from src.modules.strategy import entry_candidates
    monkeypatch.setattr(entry_candidates, 'SessionLocal', sessions)
    monkeypatch.setattr(risk, 'current_concentration', lambda: {'total_market_value':100,'by_market':{'CN':100}})
    with sessions() as db:
        db.add_all([EntryCandidate(snapshot_date='2026-09-16', stock_symbol=m, stock_market=m,
                                   stock_name=m, score=score, status='active', action='buy')
                    for m, score in [('CN',100), ('US',90)]])
        db.commit()
    result = entry_candidates.list_entry_candidates(limit=2, min_score=80)
    assert [r['score'] for r in result['items']] == [90,70]
    assert entry_candidates.list_entry_candidates(limit=1)['items'][0]['stock_market'] == 'US'
    with sessions() as db:
        assert db.query(EntryCandidate).filter_by(stock_market='CN').one().score == 100


def test_public_opportunity_and_dashboard_paths_use_same_overlay(sessions, monkeypatch):
    from src.modules.research.api import recommendations
    from src.modules.portfolio.api import dashboard
    monkeypatch.setattr(risk, 'current_concentration', lambda: {'total_market_value':100,'by_market':{'CN':100}})
    rows = [{'stock_market':m, 'stock_symbol':m, 'score':score, 'rank_score':score,
             'status':'active', 'action':'buy', 'entry_low':10}
            for m, score in [('CN',100), ('US',90)]]
    monkeypatch.setattr(recommendations, 'list_strategy_signals', lambda **kw: {'items':deepcopy(rows)})
    response = recommendations.get_strategy_signal_list()
    assert [item['rank_score'] for item in response['items']] == [90,70]
    assert response['items'][1]['ai_score'] == 7
    assert response['items'][1]['factor_explain']['negative'][-1]['contribution'] == -30
    monkeypatch.setattr(dashboard, 'get_strategy_stats', lambda **kw: {})
    monkeypatch.setattr(dashboard, 'list_strategy_signals', lambda **kw: {'items':deepcopy(rows) if kw['holding']=='unheld' else []})
    with sessions() as db:
        result = dashboard.get_dashboard_overview(market='ALL', days=30, action_limit=5, risk_limit=5, db=db)
    assert [item['rank_score'] for item in result['action_center']['opportunities']] == [90,70]
