"""Canadian review regressions through the actual API, MCP and provider boundaries."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import AnalysisHistory


@pytest.fixture
def history_db():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    pool = sessionmaker(bind=engine)
    yield pool
    engine.dispose()


def add_history(pool, key, market, content, day=None):
    with pool() as db:
        db.add(AnalysisHistory(agent_name='tradingagents', stock_symbol=key,
            analysis_date=day or date.today().isoformat(), content=content,
            raw_data={**({'market': market} if market else {}), 'suggestion': {'action': 'buy'}}))
        db.commit()


def provider_fixture(monkeypatch, market, ticker, closes):
    from marketdata.client import MarketData
    from marketdata.defaults import StaticConfigProvider
    from marketdata.ports import SourceConfig
    from marketdata.vendors import canada
    from src.platform.marketdata.collectors import kline_collector as kc
    start = date.today() - timedelta(days=35)
    timestamps = [int(datetime.combine(start + timedelta(days=i), datetime.min.time(), timezone.utc).timestamp()) + 18*3600 for i in range(len(closes))]
    urls = []
    def get(url, **kwargs):
        urls.append(url)
        return {'chart': {'result': [{'meta': {'symbol': ticker, 'exchangeName': 'TOR' if market == 'TSX' else 'VAN',
            'instrumentType': 'EQUITY', 'currency': 'CAD', 'regularMarketTime': timestamps[-1]},
            'timestamp': timestamps, 'indicators': {'quote': [{'open': closes, 'high': closes, 'low': closes,
                'close': closes, 'volume': [100]*len(closes)}]}}]}}
    md = MarketData(StaticConfigProvider({'kline': [SourceConfig(vendor='yahoo_canada')]}))
    monkeypatch.setattr(kc, 'get_market_data', lambda: md)
    monkeypatch.setattr(canada, 'market_get', get)
    return start, urls


@pytest.mark.parametrize('market,symbol,ticker', [('TSX', 'RY', 'RY.TO'), ('TSXV', 'RCK', 'RCK.V')])
def test_fallback_uses_canonical_collected_bars_and_market_separation(monkeypatch, market, symbol, ticker):
    from src.modules.automation.tradingagents import toolkit_adapter as ta
    from src.platform.marketdata.collectors.kline_collector import KlineData, clear_kline_cache
    from src.platform.marketdata.models import MarketCode
    _, urls = provider_fixture(monkeypatch, market, ticker, [123.0])
    def unavailable(*args, **kwargs):
        raise RuntimeError('Yahoo connection timeout')
    monkeypatch.setattr(ta, '_real_load_ohlcv', unavailable)
    cached = [KlineData(date='2026-01-01', open=999, high=999, low=999, close=999, volume=100)]
    with ta.panwatch_data_context({'stock': SimpleNamespace(symbol=symbol, market=MarketCode(market)), 'klines': cached}):
        assert ta._panwatch_load_ohlcv(ticker, date.today().isoformat()).iloc[0]['Close'] == 999
    assert urls == []
    # Same bare ticker on another exchange must never reuse collected bars.
    for other_market in ['US', 'TSXV' if market == 'TSX' else 'TSX']:
        clear_kline_cache()
        with ta.panwatch_data_context({'stock': SimpleNamespace(symbol=symbol, market=MarketCode(other_market)), 'klines': cached}):
            assert ta._panwatch_load_ohlcv(ticker, date.today().isoformat()).iloc[0]['Close'] == 123
    assert urls == [f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}'] * 2


@pytest.mark.parametrize('market,symbol,ticker', [('TSX', 'RY', 'RY.TO'), ('TSXV', 'RCK', 'RCK.V')])
def test_history_api_computes_canadian_returns_with_real_collector(monkeypatch, history_db, market, symbol, ticker):
    from src.modules.automation.api import agents
    from src.modules.automation.tradingagents import operations
    start, urls = provider_fixture(monkeypatch, market, ticker, [100+i for i in range(21)])
    add_history(history_db, f'{market}:{symbol}', market, 'CANADA', start.isoformat())
    monkeypatch.setattr(operations, 'SessionLocal', history_db)
    app = FastAPI()
    app.include_router(agents.router, prefix="/agents")
    response = TestClient(app).get('/agents/tradingagents/history-comparison', params={'stock_symbol': symbol, 'market': market})
    assert response.status_code == 200
    result = response.json()
    item = result['items'][0]
    assert [item[f'return_{n}d_pct'] for n in (1, 5, 20)] == [1.0, 5.0, 20.0]
    assert item['hit_20d'] is True and result['stats']['overall_hit_rate'] == 1.0
    assert urls == [f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}']


def test_invalid_history_market_rejected_before_history_query():
    from src.modules.automation.tradingagents import operations
    for market in ('BOGUS', '', None):
        with pytest.raises(ValueError):
            operations.build_history_comparison('RY', market)
    from src.platform.marketdata.models import MarketCode
    for market in MarketCode:
        assert operations._resolve_market(market.value.lower()) == market


@pytest.mark.parametrize('market,symbol', [('TSX', 'RY'), ('TSXV', 'RCK')])
def test_mcp_and_add_position_use_qualified_history_and_fractional_prompt(monkeypatch, history_db, market, symbol):
    from src.modules.administration.api import mcp
    from src.modules.research.api import insights
    from src.platform.marketdata.collectors.news_collector import NewsCollector
    add_history(history_db, symbol, 'US', 'US_ONLY_REPORT')
    add_history(history_db, f'{market}:{symbol}', market, 'CANADA_ONLY_REPORT', (date.today()-timedelta(days=1)).isoformat())
    # A later malformed record must not shadow the latest matching report.
    add_history(history_db, f'{market}:{symbol}', 'US', 'WRONG_MARKET_REPORT')
    monkeypatch.setattr(mcp, '_write_call_log', lambda *args, **kwargs: None)
    with history_db() as db:
        response = asyncio.run(mcp._handle_tools_call({'name': 'get_stock_suggestions', 'arguments': {'symbol': symbol, 'market': market}}, db, {}, 1))
    payload = json.loads(response.body)
    text = payload['result']['content'][0]['text']
    assert response.status_code == 200 and not payload['result']['isError']
    assert 'CANADA_ONLY_REPORT' in text and 'US_ONLY_REPORT' not in text and 'WRONG_MARKET_REPORT' not in text
    prompts = []
    class FakeAI:
        async def chat(self, system, user, **kwargs):
            prompts.append(user)
            return '结论: 谨慎'
    async def no_news(*args, **kwargs): return []
    async def no_technical(*args, **kwargs): return ''
    monkeypatch.setattr(insights, 'md_quote_rows', lambda *args, **kwargs: [])
    monkeypatch.setattr(insights, 'fetch_technical_context', no_technical)
    monkeypatch.setattr(NewsCollector, 'from_database', lambda: SimpleNamespace(fetch_all=no_news))
    monkeypatch.setattr(insights, 'get_configured_failover_client', lambda *args, **kwargs: FakeAI())
    def database():
        with history_db() as db: yield db
    app = FastAPI()
    app.include_router(insights.router)
    app.dependency_overrides[get_db] = database
    response = TestClient(app).post('/add-position-eval', json={'symbol': symbol, 'market': market,
        'current_quantity': .125, 'current_cost': 100, 'add_quantity': .125, 'add_price': 110})
    assert response.status_code == 200 and response.json()['total_quantity'] == .25
    assert response.json()['new_cost'] == 105
    assert '当前持仓 0.125 股' in prompts[0] and '拟加仓 0.125 股' in prompts[0]
    assert 'CANADA_ONLY_REPORT' in prompts[0] and 'US_ONLY_REPORT' not in prompts[0] and 'WRONG_MARKET_REPORT' not in prompts[0]


def test_safe_legacy_history_behavior(history_db):
    from src.modules.assistant.tool_adapters import build_stock_context
    add_history(history_db, 'RY', None, 'LEGACY_REPORT', (date.today()-timedelta(days=1)).isoformat())
    add_history(history_db, 'RY', 'HK', 'WRONG_MARKET_REPORT')
    with history_db() as db:
        assert 'LEGACY_REPORT' in build_stock_context(db, 'RY', 'US')
        assert 'WRONG_MARKET_REPORT' not in build_stock_context(db, 'RY', 'US')
        assert build_stock_context(db, 'RY', 'TSX') == ''


def test_history_api_rejects_unknown_market():
    from src.modules.automation.api import agents
    app = FastAPI()
    app.include_router(agents.router, prefix='/agents')
    response = TestClient(app).get('/agents/tradingagents/history-comparison', params={'stock_symbol': 'RY', 'market': 'BOGUS'})
    assert response.status_code == 400
