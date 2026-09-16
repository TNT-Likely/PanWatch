import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.platform.persistence.database import Base
from src.platform.persistence.models import Account
from src.modules.portfolio.api import accounts


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def test_enabled_cash_includes_cash_only_accounts(db):
    db.add_all([Account(name='invested', available_funds=200), Account(name='cash only', available_funds=100), Account(name='disabled', available_funds=9999, enabled=False)])
    db.commit()
    assert accounts._gather_account_totals(db, market_value=250) == {
        'available_funds': 300, 'total_assets': 550, 'equity_ratio': 250/550}


@pytest.mark.parametrize('cash,mv,ratio', [(0,100,1), (0,0,None), (-100,100,None), (-150,100,None)])
def test_zero_cash_and_nonpositive_assets(db, cash, mv, ratio):
    db.add(Account(name='test', available_funds=cash))
    db.commit()
    assert accounts._gather_account_totals(db, market_value=mv)['equity_ratio'] == ratio


def test_review_keeps_internal_distribution_separate_from_total_exposure(db, monkeypatch):
    from src.modules.portfolio import portfolio_benchmark as benchmark
    from src.platform.ai import ai_failover
    db.add(Account(name='test', available_funds=300))
    db.commit()
    holdings = [{'symbol':'TEST','market':'CN','market_value':250,'unrealized_pnl':0}]
    monkeypatch.setattr(accounts, '_gather_holdings', lambda _: holdings)
    monkeypatch.setattr(benchmark, 'build_portfolio_benchmark', lambda *a, **kw: {})
    monkeypatch.setattr(benchmark, 'build_attribution', lambda *a, **kw: [])
    client = AsyncMock()
    client.chat.return_value = '持仓内部集中度：CN 100%；总资产敞口：45.5%'
    monkeypatch.setattr(ai_failover, 'get_configured_failover_client', lambda *a: client)
    result = asyncio.run(accounts.portfolio_ai_review(db=db))
    system, context = client.chat.call_args.args
    assert '持仓内部集中度' in system and '总资产敞口' in system
    assert '总资产 550 CNY' in context and '45.5%' in context
    assert result['diagnostics']['by_market'] == {'CN':250}
    assert result['diagnostics']['max_weight'] == 1
    assert result['account_totals']['available_funds'] == 300
