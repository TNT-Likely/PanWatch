"""Reconstructed Canadian valuation, currency isolation and fractional persistence tests."""
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.platform.persistence.database import Base
from src.platform.persistence.models import Account, Stock, Position
from src.modules.portfolio.api import accounts as api
from src.modules.portfolio.portfolio_diagnostics import diagnose_positions


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def holding(db, market='TSX', symbol='RY', quantity=1.5):
    account = db.query(Account).first()
    if not account:
        account = Account(name='Portfolio', available_funds=100, enabled=True)
        db.add(account)
        db.flush()
    stock = Stock(symbol=symbol, market=market, name=symbol)
    db.add(stock)
    db.flush()
    db.add(Position(account_id=account.id, stock_id=stock.id, quantity=quantity, cost_price=10))
    db.commit()
    return account, stock


@pytest.fixture
def rates(monkeypatch):
    monkeypatch.setattr(api, 'get_cad_cny_rate', lambda **kw: 5.0)
    monkeypatch.setattr(api, 'get_canada_usd_cny_rate', lambda **kw: 7.0)
    monkeypatch.setattr(api, 'get_usd_cny_rate', lambda: 7.0)
    monkeypatch.setattr(api, 'get_hkd_cny_rate', lambda: .9)


def test_cad_usd_and_cross_market_symbol_collision(db, monkeypatch, rates):
    holding(db)
    holding(db, 'US', quantity=2)
    holding(db, 'TSXV', 'DUAL', quantity=.25)
    monkeypatch.setattr(api, '_fetch_quotes_for_stocks', lambda stocks: {
        'TSX:RY': {'current_price': 20, 'currency': 'CAD', 'source_timestamp': '2026-10-09T20:00:00+00:00'},
        'US:RY': {'current_price': 30, 'currency': 'USD'},
        'TSXV:DUAL': {'current_price': 40, 'currency': 'USD'},
    })
    result = api.get_portfolio_summary(db=db)
    positions = result['accounts'][0]['positions']
    assert [p['market_value_cny'] for p in positions] == [150, 420, 70]
    assert [p['currency'] for p in positions] == ['CAD', 'USD', 'USD']
    assert positions[0]['source_timestamp'] == '2026-10-09T20:00:00+00:00'
    assert result['total']['total_market_value'] == 640
    assert result['exchange_rates']['CAD_CNY'] == 5
    assert result['exchange_rates']['CANADA_USD_CNY'] == 7
    assert result['total']['valuation_incomplete'] is False


@pytest.mark.parametrize('currency,rate,status', [('CAD', None, 'fx_unavailable'), (None, 5, 'currency_unavailable')])
def test_unavailable_conversion_is_explicit(db, monkeypatch, rates, currency, rate, status):
    holding(db)
    holding(db, 'CN', '600000', 2)
    monkeypatch.setattr(api, 'get_cad_cny_rate', lambda **kw: rate)
    monkeypatch.setattr(api, '_fetch_quotes_for_stocks', lambda stocks: {
        'TSX:RY': {'current_price': 20, 'currency': currency},
        'CN:600000': {'current_price': 30},
    })
    summary = api.get_portfolio_summary(db=db)
    p = summary['accounts'][0]['positions'][0]
    assert p['valuation_status'] == status
    assert p['market_value'] == 30
    assert p['market_value_cny'] is None and p['exchange_rate'] is None
    assert summary['total']['valuation_incomplete'] is True
    assert summary['accounts'][0]['valuation_incomplete'] is True
    assert summary['total']['total_market_value'] == 60
    holdings = api._gather_holdings(db)
    assert holdings[0]['fx'] is None and holdings[0]['market_value'] is None
    diagnostic = diagnose_positions(holdings)
    assert diagnostic['valuation_incomplete'] is True
    assert {'code': 'valuation_incomplete'} in diagnostic['alert_details']
    assert api.portfolio_benchmark(db=db)['reason'] == 'valuation_incomplete'
    assert api.portfolio_attribution(db=db)['reason'] == 'valuation_incomplete'


def test_quote_map_qualified_with_safe_legacy_fallback(monkeypatch):
    monkeypatch.setattr(api, 'md_quote_rows', lambda symbols, market: [{'symbol': 'RY', 'current_price': 20 if market == 'TSX' else 30}])
    stocks = [SimpleNamespace(symbol='RY', market=m) for m in ('TSX', 'US')]
    quotes = api._fetch_quotes_for_stocks(stocks)
    assert quotes['TSX:RY']['current_price'] == 20
    assert quotes['US:RY']['current_price'] == 30
    assert api._position_quote({'RY': {'current_price': 99}}, stocks[0], stocks) is None
    assert api._position_quote({'RY': {'current_price': 99}}, stocks[0], stocks[:1])['current_price'] == 99


def test_fractional_quantity_validation_and_existing_sqlite(db):
    account, stock = holding(db)
    api.update_position(db.query(Position).first().id, api.PositionUpdate(quantity=.125), db)
    assert db.query(Position).first().quantity == .125
    for market in ('CN', 'HK', 'US'):
        with pytest.raises(HTTPException):
            api._validate_quantity(.5, market)
        api._validate_quantity(2, market)
    for quantity in (0, -1, float('inf'), float('nan')):
        with pytest.raises(ValidationError):
            api.PositionCreate(account_id=account.id, stock_id=stock.id, cost_price=10, quantity=quantity)
    conn = sqlite3.connect(':memory:')
    conn.execute('create table positions (quantity INTEGER NOT NULL)')
    conn.execute('insert into positions values (?)', (.125,))
    assert conn.execute('select quantity, typeof(quantity) from positions').fetchone() == (.125, 'real')
    conn.close()


def test_reference_fx_validated_and_cached(monkeypatch):
    monkeypatch.setattr(api, '_cad_rate_cache', {'rate': None, 'ts': 0})
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            'base': 'CAD', 'date': datetime.now(timezone.utc).date().isoformat(), 'rates': {'CNY': 5.2}})
    monkeypatch.setattr(api.httpx, 'get', get)
    assert api.get_cad_cny_rate() == api.get_cad_cny_rate() == 5.2
    assert len(calls) == 1 and calls[0][1]['timeout'] == 5
    assert calls[0][1]['params'] == {'base': 'CAD', 'symbols': 'CNY'}


@pytest.mark.parametrize('rate,age', [(0, 0), (-1, 0), (float('nan'), 0), (5, 8), (5, -1)])
def test_invalid_fx_never_uses_expired_fallback(monkeypatch, rate, age):
    monkeypatch.setattr(api, '_cad_rate_cache', {'rate': 5.1, 'ts': time.time() - 7200})
    date = (datetime.now(timezone.utc).date() - timedelta(days=age)).isoformat()
    monkeypatch.setattr(api.httpx, 'get', lambda *a, **kw: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {'base': 'CAD', 'date': date, 'rates': {'CNY': rate}}))
    assert api.get_cad_cny_rate() is None
    assert api.get_cad_cny_rate(refresh=False) is None


def test_metadata_refresh_uses_verified_caches_without_network(db, monkeypatch):
    holding(db)
    monkeypatch.setattr(api, '_cad_rate_cache', {'rate': 5.1, 'ts': time.time()})
    monkeypatch.setattr(api, '_canada_usd_rate_cache', {'rate': 7.1, 'ts': time.time()})
    monkeypatch.setattr(api.httpx, 'get', lambda *a, **kw: pytest.fail('metadata refresh must not fetch FX'))
    monkeypatch.setattr(api, '_fetch_quotes_for_stocks', lambda *a: pytest.fail('metadata refresh must not fetch quotes'))
    result = api.get_portfolio_summary(include_quotes=False, refresh_exchange_rates=False, db=db)
    assert result['exchange_rates']['CAD_CNY'] == 5.1
    assert result['exchange_rates']['CANADA_USD_CNY'] == 7.1
    assert result['accounts'][0]['positions'][0]['currency'] is None
    assert result['total']['valuation_incomplete'] is True


def test_canadian_usd_cannot_inherit_synthetic_default(db, monkeypatch, rates):
    holding(db, symbol='USD.U')
    monkeypatch.setattr(api, 'get_canada_usd_cny_rate', lambda **kw: None)
    monkeypatch.setattr(api, '_fetch_quotes_for_stocks', lambda stocks: {'TSX:USD.U': {'current_price': 20, 'currency': 'USD'}})
    p = api.get_portfolio_summary(db=db)['accounts'][0]['positions'][0]
    assert p['valuation_status'] == 'fx_unavailable' and p['market_value_cny'] is None


def test_missing_quote_does_not_become_cost_basis_or_negative_pnl(db, monkeypatch, rates):
    holding(db)
    holding(db, 'CN', '600000', 2)
    monkeypatch.setattr(api, '_fetch_quotes_for_stocks', lambda stocks: {'TSX:RY': {'current_price': 20, 'currency': 'CAD'}})
    result = api.get_portfolio_summary(db=db)
    assert result['total']['total_pnl'] == 75
    assert result['total']['total_pnl_pct'] == 100
    assert result['total']['valuation_incomplete'] is True
    assert api._gather_holdings(db)[1]['market_value'] is None


def test_template_fractional_roundtrip(db):
    from src.modules.automation.api.templates import TemplatePayload, export_template, import_template
    holding(db, quantity=.125)
    payload = export_template(include_internal=False, modules='portfolio', db=db)
    assert payload['accounts'][0]['positions'][0]['quantity'] == .125
    parsed = TemplatePayload.model_validate(payload)
    import_template(parsed, mode='merge', modules='portfolio', db=db)
    assert db.query(Position).first().quantity == .125


@pytest.mark.parametrize('market', ['CN', 'HK', 'US'])
def test_template_rejects_other_market_fractions(market):
    from src.modules.automation.api.templates import TemplatePosition
    with pytest.raises(ValidationError):
        TemplatePosition(symbol='RY', name='RY', market=market, cost_price=10, quantity=.125)


def test_dashboard_canadian_cost_is_marked_incomplete(db, monkeypatch):
    from src.modules.portfolio.api import dashboard
    holding(db)
    monkeypatch.setattr(dashboard, 'get_strategy_stats', lambda **kw: {})
    monkeypatch.setattr(dashboard, 'list_strategy_signals', lambda **kw: {'items': []})
    result = dashboard.get_dashboard_overview(market='ALL', action_limit=6, risk_limit=6, days=45, db=db)
    assert result['portfolio']['valuation_incomplete'] is True
    assert result['portfolio']['invested_cost'] == 0
    assert result['portfolio']['by_market'][0]['valuation_incomplete'] is True


def test_metadata_refresh_can_fetch_both_reference_rates(db, monkeypatch):
    holding(db)
    seen = []
    monkeypatch.setattr(api, 'get_cad_cny_rate', lambda **kw: seen.append(('CAD', kw['refresh'])) or 5)
    monkeypatch.setattr(api, 'get_canada_usd_cny_rate', lambda **kw: seen.append(('USD', kw['refresh'])) or 7)
    result = api.get_portfolio_summary(include_quotes=False, refresh_exchange_rates=True, db=db)
    assert seen == [('CAD', True), ('USD', True)]
    assert result['exchange_rates']['CANADA_USD_CNY'] == 7


def test_failed_reference_fx_never_returns_synthetic_rate(monkeypatch):
    monkeypatch.setattr(api, '_canada_usd_rate_cache', {'rate': 7, 'ts': time.time() - 7200})
    def fail(*a, **kw):
        raise TimeoutError('provider timeout')
    monkeypatch.setattr(api.httpx, 'get', fail)
    assert api.get_canada_usd_cny_rate() is None


def test_rejected_non_canadian_update_does_not_mutate_cost(db):
    holding(db, 'US', quantity=2)
    position = db.query(Position).first()
    with pytest.raises(HTTPException):
        api.update_position(position.id, api.PositionUpdate(cost_price=123, quantity=.5), db)
    assert position.cost_price == 10 and position.quantity == 2


def test_create_fractional_canadian_holding(db):
    a = Account(name='New', enabled=True)
    s = Stock(symbol='RY', market='TSX', name='RY')
    db.add_all([a, s])
    db.commit()
    created = api.create_position(api.PositionCreate(account_id=a.id, stock_id=s.id, cost_price=10, quantity=.125), db)
    assert created['quantity'] == .125


@pytest.mark.parametrize('model_name', ['TemplateStock', 'TemplatePosition'])
def test_template_canonical_identity_and_conflict(model_name):
    from src.modules.automation.api import templates
    cls=getattr(templates,model_name)
    extra={'quantity':.125,'cost_price':10} if model_name=='TemplatePosition' else {}
    assert cls(symbol='rei.un.to',name='RioCan',market='TSX',**extra).symbol=='REI-UN'
    assert cls(symbol='RCK.V',name='Rock Tech',market='TSXV',**extra).symbol=='RCK'
    with pytest.raises(ValueError): cls(symbol='RY.TO',name='Royal',market='US',**extra)
