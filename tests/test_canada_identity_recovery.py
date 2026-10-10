"""Reconstructed Canadian identity/history regression coverage after workspace loss."""
from datetime import date
from types import SimpleNamespace

import pytest

from src.platform.marketdata.instrument_identity import (
    analysis_key, instrument_get, scoped_keys, unambiguous_stock_map,
)
from src.platform.marketdata.models import MarketCode


def test_identity_helpers_keep_legacy_keys_only_if_unambiguous():
    assert scoped_keys([('RY', 'US'), ('RY', 'TSX'), ('ZSP', 'TSX')]) == {
        ('US', 'RY'): 'US:RY', ('TSX', 'RY'): 'TSX:RY', ('TSX', 'ZSP'): 'ZSP',
    }
    assert analysis_key('RY', 'TSX') == 'TSX:RY'
    assert analysis_key('RY', 'US') == 'RY'
    assert analysis_key('RCK', 'TSXV') == 'TSXV:RCK'
    assert instrument_get({'RY': {'market': 'US'}}, 'RY', 'TSX') is None
    assert instrument_get({'TSX:RY': {'market': 'TSX', 'price': 250}, 'RY': {'market': 'US'}}, 'RY', 'TSX')['price'] == 250
    assert unambiguous_stock_map([SimpleNamespace(symbol='RY'), SimpleNamespace(symbol='RY')]) == {}


def test_dual_listing_positions_and_signal_packs_never_merge(monkeypatch):
    import asyncio
    from src.modules.automation.base import AccountInfo, PortfolioInfo, PositionInfo
    import src.modules.research.signals.signal_pack as signals
    positions = [PositionInfo(account_id=1, account_name='QA', stock_id=i, symbol='RY', name='Royal Bank',
                              market=m, cost_price=c, quantity=q)
                 for i, m, c, q in [(1, MarketCode.US, 100, 10), (2, MarketCode.TSX, 140, 20)]]
    portfolio = PortfolioInfo(accounts=[AccountInfo(id=1, name='QA', available_funds=0, positions=positions)])
    assert portfolio.get_positions_for_stock('RY') == []
    assert portfolio.get_aggregated_position('RY', MarketCode.TSX)['total_quantity'] == 20
    assert portfolio.get_aggregated_position('RY', MarketCode.US)['avg_cost'] == 100
    monkeypatch.setattr(signals.SignalPackBuilder, '_source_policy', staticmethod(lambda *a, **k: ([('yahoo_canada', {}), ('tencent', {})], False)))
    monkeypatch.setattr(signals, 'md_stock_data', lambda symbols, market: [SimpleNamespace(symbol='RY', market=MarketCode(market), current_price=250 if market == 'TSX' else 180)])
    monkeypatch.setattr(signals.KlineCollector, 'get_kline_summary', lambda self, symbol: {'marker': self.market.value})
    result = asyncio.run(signals.SignalPackBuilder().build_for_symbols(
        symbols=[('RY', MarketCode.US, 'RY US'), ('RY', MarketCode.TSX, 'RY CAD')],
        include_news=False, news_hours=1, portfolio=portfolio))
    assert set(result) == {'US:RY', 'TSX:RY'}
    canadian = instrument_get(result, 'RY', MarketCode.TSX)
    assert canadian.quote.current_price == 250 and canadian.technical['marker'] == 'TSX'
    assert canadian.position.aggregated['total_quantity'] == 20
    assert instrument_get(result, 'RY', MarketCode.US).quote.current_price == 180


def test_deep_analysis_cache_uses_qualified_canadian_identity(monkeypatch):
    import src.modules.automation.tradingagents.agent as module
    agent = module.TradingAgentsAgent.__new__(module.TradingAgentsAgent)
    agent.cache_ttl_hours = 12
    requested = []
    history = SimpleNamespace(title='Canadian report', content='Canadian evidence', raw_data={'market': 'TSX'})
    monkeypatch.setattr(module, 'get_analysis', lambda **kwargs: (requested.append(kwargs['stock_symbol']), history)[1])
    stock = SimpleNamespace(symbol='RY', market=MarketCode.TSX, name='Royal Bank')
    assert agent._try_cache_hit(stock).content == 'Canadian evidence'
    assert requested == ['TSX:RY']
    assert agent._try_cache_hit(SimpleNamespace(symbol='RY', market=MarketCode.US, name='US Royal Bank')) is None


def test_qualified_history_backfill_and_comparison_keep_canadian_scope(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from src.platform.persistence.database import Base
    from src.platform.persistence.models import AnalysisHistory
    from src.modules.automation.tradingagents import operations
    from src.modules.automation import suggestion_pool
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    pool = sessionmaker(bind=engine)
    with pool() as db:
        for key, price, market in [('RY', 100, 'US'), ('TSX:RY', 250, 'TSX')]:
            db.add(AnalysisHistory(agent_name='tradingagents', stock_symbol=key, analysis_date=date.today().isoformat(),
                                   content='QA', raw_data={'market': market, 'price_at_analysis': price, 'suggestion': {'action': 'hold'}}))
        db.commit()
    monkeypatch.setattr(operations, 'SessionLocal', pool)
    monkeypatch.setattr(operations.KlineCollector, 'get_klines', lambda *a, **kw: [])
    calls = []
    monkeypatch.setattr(suggestion_pool, 'save_suggestion', lambda **kw: (calls.append(kw), True)[1])
    result = operations.backfill_tradingagents_suggestions()
    assert result['skipped'] == 1 and len(calls) == 1 and calls[0]['stock_symbol'] == 'RY'
    history = operations.build_history_comparison(stock_symbol='RY', market='TSX')
    assert len(history['items']) == 1 and history['items'][0]['price_at_analysis'] == 250
    assert operations.build_history_comparison(stock_symbol='RY', market='US')['items'][0]['price_at_analysis'] == 100
    engine.dispose()


def test_trace_lookup_does_not_reuse_other_exchange():
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from src.platform.persistence.database import Base
    from src.platform.persistence.models import AgentRun
    from src.modules.automation.agent_runs import find_active_tradingagents_trace
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(AgentRun(agent_name='tradingagents', trace_id='man-tradingagents-RY-123', status='running', created_at=datetime.now(timezone.utc)))
        db.commit()
        assert find_active_tradingagents_trace(db, analysis_key('RY', 'TSX')) is None
        assert find_active_tradingagents_trace(db, analysis_key('RY', 'US')) == 'man-tradingagents-RY-123'
    engine.dispose()
