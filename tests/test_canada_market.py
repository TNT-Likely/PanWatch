from datetime import date, datetime, timezone
import pytest
from src.platform.marketdata.models import MarketCode, MARKETS
from src.platform.scheduling import trading_calendar as calendar


def test_canadian_calendar_dst_holidays_and_unknown_year():
    for code in ['TSX','TSXV']:
        assert MARKETS[MarketCode(code)].timezone=='America/Toronto'
        assert not calendar.is_trading_day(code,date(2026,10,12))
        assert calendar.is_trading_day(code,date(2026,11,26))  # US Thanksgiving is not Canadian
        assert calendar.trading_sessions(code,date(2026,12,24))[0].end.hour==13
        assert not calendar.calendar_known(code,date(2027,1,4))
        assert calendar.market_status(code,datetime(2026,7,2,14,tzinfo=timezone.utc))=='trading'
        assert calendar.market_status(code,datetime(2026,1,6,14,tzinfo=timezone.utc))=='pre_market'


def test_canadian_analysis_date():
    from src.platform.marketdata.instrument_identity import analysis_trade_date
    now=datetime(2026,10,10,2,tzinfo=timezone.utc)
    assert analysis_trade_date('TSX',now)=='2026-10-09'
    assert analysis_trade_date('TSXV',datetime(2026,1,6,4,tzinfo=timezone.utc))=='2026-01-05'
    assert analysis_trade_date('CN',now)==now.astimezone().date().isoformat()
    with pytest.raises(ValueError): analysis_trade_date('TSX',datetime(2026,10,10))


def test_stock_create_identity():
    from src.modules.market.api.stocks import StockCreate
    assert StockCreate(symbol='rei.un.to',name='RioCan',market='TSX').symbol=='REI-UN'
    with pytest.raises(ValueError): StockCreate(symbol='RY.TO',name='Royal',market='US')


def test_discovery_is_explicitly_unavailable():
    from src.modules.market.api.discovery import _normalize_market
    from fastapi import HTTPException
    with pytest.raises(HTTPException): _normalize_market('TSX')


def test_canadian_metadata_and_portfolio_identity():
    from types import SimpleNamespace as N
    from src.modules.automation.tradingagents.data_context import build_stock_metadata_context, to_tradingagents_portfolio
    prompt=build_stock_metadata_context('ZSP','BMO ETF','TSX')
    assert 'Canadian' in prompt and 'ETF' in prompt and 'CNY' in prompt


def test_all_market_search_preserves_legacy_on_canada_outage(monkeypatch):
    from src.platform.marketdata import stock_list
    from marketdata.vendors import canada
    monkeypatch.setattr(stock_list,'_realtime_search',lambda *a:[{'market':'US','symbol':'RY','name':'Royal'}])
    monkeypatch.setattr(stock_list,'_cached_search',lambda *a:[])
    def unavailable(*a,**kw): raise RuntimeError('Yahoo unavailable')
    monkeypatch.setattr(canada,'search_canadian',unavailable)
    assert stock_list.search_stocks('RY')[0]['market']=='US'


def test_actual_batch_endpoint_keeps_canadian_valuation_metadata(monkeypatch):
    import asyncio
    from src.modules.market.api import quotes
    from src.platform.marketdata.marketdata_client import _quote_to_row
    from marketdata.types import Quote
    q=Quote(symbol='ZSP-U',market='TSX',current_price=42,currency='USD',security_type='ETF',delayed=True,
            source='yahoo_canada',source_timestamp=datetime(2026,10,9,20,tzinfo=timezone.utc))
    monkeypatch.setattr(quotes,'md_quote_rows',lambda *a:[_quote_to_row(q)])
    rows=asyncio.run(quotes.get_quotes_batch(quotes.QuoteBatchRequest(items=[quotes.QuoteItem(symbol='ZSP-U',market='TSX')])))
    assert rows[0]['currency']=='USD' and rows[0]['is_delayed'] is True
    assert rows[0]['source_timestamp']=='2026-10-09T20:00:00+00:00'
    assert quotes._quote_to_response('MISSING',MarketCode.TSX,None)['currency'] is None


def test_analyze_carries_metadata_and_toronto_save_cache_date(monkeypatch):
    import asyncio
    from types import SimpleNamespace as N
    from unittest.mock import MagicMock
    from src.modules.automation.tradingagents import agent as module
    from src.modules.automation import suggestion_pool
    agent=module.TradingAgentsAgent()
    agent._available=True
    agent.emit_paper_trading_signal=False
    stock=N(symbol='ZSP-U',market=MarketCode.TSX,name='Canadian USD ETF')
    context=N(_force_refresh=True,_trace_id='qa',report_language='en-US',ai_client=MagicMock(),model_label='QA',portfolio=None)
    saved=[]; metadata=[]; dates=[]
    monkeypatch.setattr(module,'build_ta_llm_config',lambda *a,**k:{'deep_think_llm':'QA','quick_think_llm':'QA'})
    monkeypatch.setattr(module,'build_stock_metadata_context',lambda **kw:(metadata.append(kw),'metadata')[1])
    monkeypatch.setattr(agent,'_run_tradingagents_sync',lambda **kw:{})
    monkeypatch.setattr(agent,'_collect_toolkit_diagnostic',lambda *a:{})
    monkeypatch.setattr(module,'map_state_to_result',lambda **kw:N(raw_data={},content='QA',title='QA'))
    monkeypatch.setattr(module,'save_analysis',lambda **kw:saved.append(kw))
    monkeypatch.setattr(suggestion_pool,'save_suggestion',lambda **kw:None)
    monkeypatch.setattr(module,'analysis_trade_date',lambda *a:'2026-10-09')
    quote={'current_price':42,'currency':'USD','security_type':'ETF','source_timestamp':'2026-10-09T20:00:00+00:00'}
    asyncio.run(agent.analyze(context,{'stock':stock,'quote':quote}))
    assert metadata[0]['currency']=='USD' and metadata[0]['security_type']=='ETF'
    assert metadata[0]['source_timestamp']==quote['source_timestamp']
    assert saved[0]['stock_symbol']=='TSX:ZSP-U' and saved[0]['analysis_date']==date(2026,10,9)
    monkeypatch.setattr(module,'get_analysis',lambda **kw:(dates.append(kw),None)[1])
    agent._try_cache_hit(stock)
    assert dates[0]['analysis_date']==saved[0]['analysis_date']
