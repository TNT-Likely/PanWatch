"""Actual ContextBuilder -> scheduled prompt currency boundaries.

Only unrelated source/history I/O and HTTP reference FX transport are controlled.
No live model or notification delivery is represented by these regressions.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as N

import pytest

from src.modules.automation.base import AccountInfo, AgentContext, PortfolioInfo, PositionInfo
from src.modules.automation.daily_report import DailyReportAgent
from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.modules.automation.premarket_outlook import PremarketOutlookAgent
from src.modules.portfolio.api import accounts
from src.modules.research.context_builder import ContextBuilder
from src.modules.research import context_builder
from src.platform.marketdata import reference_fx
from src.modules.research.signals.signal_pack import SignalPack
from src.platform.marketdata.models import MarketCode, StockData
from src.platform.runtime.config import AppConfig, Settings, StockConfig


@pytest.fixture
def research(monkeypatch):
    monkeypatch.setattr(ContextBuilder, '_get_kline_history', lambda *a, **k: {'available': False})
    monkeypatch.setattr(ContextBuilder, '_get_index_context', lambda *a, **k: {'available': False})
    monkeypatch.setattr(ContextBuilder, '_build_snapshot_memory', lambda *a, **k: {})
    monkeypatch.setattr(ContextBuilder, '_load_history_news', lambda *a, **k: [])
    monkeypatch.setattr(context_builder, 'get_latest_ta_verdict', lambda *a, **k: None)
    monkeypatch.setattr(reference_fx, 'CAD_RATE_CACHE', {'rate': None, 'ts': 0})
    monkeypatch.setattr(reference_fx, 'USD_RATE_CACHE', {'rate': None, 'ts': 0})
    monkeypatch.setattr(reference_fx, 'HKD_RATE_CACHE', {'rate': None, 'ts': 0})
    calls = []
    rates = {'CAD': 5, 'USD': 7, 'HKD': 0.9}
    def response(url, *, params, timeout):
        assert timeout == 5
        currency = params['base']
        calls.append(currency)
        if rates.get(currency) is None:
            raise OSError('controlled FX outage')
        return N(raise_for_status=lambda: None, json=lambda: {
            'base': currency, 'date': datetime.now(timezone.utc).date().isoformat(), 'rates': {'CNY': rates[currency]}})
    monkeypatch.setattr(accounts.httpx, 'get', response)

    def build(currency='CAD', market='TSX', quote_present=True, add_other_market='CN'):
        code = MarketCode(market)
        positions = [PositionInfo(1, 'Account', 1, 'T', 'TELUS', code, 10, 100)]
        watchlist = [StockConfig('T', 'TELUS', code)]
        if add_other_market:
            # Same symbol for US/Canadian listings checks scoped valuation too.
            other_symbol = 'T' if add_other_market == 'US' else '600519'
            positions.append(PositionInfo(1, 'Account', 2, other_symbol, 'Other', MarketCode(add_other_market), 10, 100))
            watchlist.append(StockConfig(other_symbol, 'Other', MarketCode(add_other_market)))
        portfolio = PortfolioInfo([AccountInfo(1, 'Account', 1000, positions)])
        context = AgentContext(None, None, AppConfig(Settings(_env_file=None), watchlist), portfolio)
        quote = StockData('T', 'TELUS', code, 20, 1, 0.2, 100, 0, 19, 20, 19, 19.8,
                          currency=currency, security_type='EQUITY', source_timestamp=datetime(2026, 10, 9, 19, 30, tzinfo=timezone.utc),
                          source='yahoo_canada', delayed=True) if quote_present else None
        packs = {f'{market}:T': SignalPack('T', 'TELUS', code, '', quote=quote, technical={'last_close': 20})}
        result = asyncio.run(ContextBuilder().build_symbol_contexts(agent_name='daily_report', context=context, packs=packs, persist_snapshot=False))
        key = f'{market}:T' if add_other_market == 'US' else 'T'
        constraints = result['symbols'][key]['constraints']
        data = {'indices': [], 'us_indices': [], 'news': [], 'signal_packs': packs,
                'symbol_contexts': result['symbols'], 'portfolio_valuation': result['portfolio_valuation'],
                'stock_data': quote, 'symbol_context': result['symbols'][key]}
        return context, constraints, data
    return N(build=build, calls=calls, rates=rates)


@pytest.mark.parametrize('market,currency,rate', [('TSX', 'CAD', 5), ('TSXV', 'CAD', 5), ('TSX', 'USD', 7)])
def test_verified_fx_is_applied_before_cost_ratios(research, market, currency, rate):
    context, constraints, data = research.build(currency=currency, market=market)
    assert constraints['total_cost'] == 1000 * rate + 1000
    assert constraints['single_position_ratio'] == round(rate / (rate + 1), 4)
    assert constraints['position']['total_cost'] == 1000  # native, never overwritten
    assert constraints['position']['currency'] == currency
    assert constraints['position']['total_cost_cny'] == 1000 * rate
    assert constraints['cash_currency'] == 'CNY'
    assert not constraints['valuation_incomplete']
    assert research.calls == [currency]
    assert constraints['fx'][currency]['source_date']


@pytest.mark.parametrize('agent', [DailyReportAgent, PremarketOutlookAgent, IntradayMonitorAgent])
@pytest.mark.parametrize('currency', ['CAD', 'USD'])
def test_all_scheduled_prompts_preserve_currency_and_quote_provenance(research, agent, currency):
    context, constraints, data = research.build(currency=currency)
    text = agent().build_prompt(data, context)[1]
    assert f'报价币种 {currency}' in text
    assert 'TSX:T' in text
    assert 'yahoo_canada' in text
    assert '2026-10-09T19:30:00+00:00' in text
    assert '供应商延迟 是' in text
    assert '不能视为当前实时行情' in text
    assert 'CNY' in text
    assert '50.0%' not in text
    if agent is IntradayMonitorAgent:
        assert f'持仓市值：2000.00 {currency}' in text
        assert '持仓市值：2000 元' not in text
        assert '成交量：100 股' in text
    if agent is DailyReportAgent:
        assert '总持仓成本2000元' not in text
        assert f'{currency}/CNY' in text


@pytest.mark.parametrize('agent', [DailyReportAgent, PremarketOutlookAgent, IntradayMonitorAgent])
def test_missing_fx_stays_unavailable_in_constraints_and_prompts(research, agent):
    research.rates['CAD'] = None
    context, constraints, data = research.build()
    assert constraints['valuation_incomplete']
    assert constraints['total_cost'] is None
    assert constraints['single_position_ratio'] is None
    assert constraints['risk_budget_hint'] == 'unavailable'
    assert constraints['position']['total_cost_cny'] is None
    text = agent().build_prompt(data, context)[1]
    assert '仓位占比 不可用' in text
    assert '仓位占比 0.0%' not in text
    assert 'CAD' in text and 'CNY' in text


@pytest.mark.parametrize('currency,quote_present', [(None, True), ('EUR', True), (None, False)])
def test_unknown_canadian_currency_does_not_default_to_cad(research, currency, quote_present):
    context, constraints, data = research.build(currency=currency, quote_present=quote_present)
    assert constraints['position']['currency'] is None
    assert constraints['valuation_incomplete']
    assert constraints['single_position_ratio'] is None
    assert research.calls == []
    text = DailyReportAgent().build_prompt(data, context)[1]
    assert '报价币种 未知' in text
    assert '总持仓成本 不可用' in text
    if not quote_present:
        assert '浮盈不可用' in text
        canadian_section = text.split('### TELUS', 1)[1].split('### Other', 1)[0]
        assert '浮盈-100.0%' not in canadian_section


def test_namesake_us_and_canadian_positions_use_separate_currency_costs(research):
    context, constraints, data = research.build(add_other_market='US')
    assert constraints['total_cost'] == 12000
    assert constraints['single_position_ratio'] == round(5000 / 12000, 4)
    assert set(research.calls) == {'CAD', 'USD'}
    assert data['portfolio_valuation']['positions']['TSX:T']['currency'] == 'CAD'
    assert data['portfolio_valuation']['positions']['US:T']['currency'] == 'USD'


def test_canadian_usd_does_not_use_legacy_synthetic_usd(research, monkeypatch):
    monkeypatch.setattr(accounts, '_usd_rate_cache', {'rate': 12345, 'ts': 99999999999})
    research.rates['USD'] = None
    _, constraints, _ = research.build(currency='USD')
    assert constraints['single_position_ratio'] is None
    assert constraints['position']['exchange_rate'] is None


def test_single_currency_cn_legacy_behavior_is_preserved():
    position = PositionInfo(1, 'CN', 1, '600519', 'CN', MarketCode.CN, 10, 100)
    result = ContextBuilder._build_portfolio_constraints(PortfolioInfo([AccountInfo(1, 'CN', 1000, [position])]), '600519', MarketCode.CN)
    assert result['total_cost'] == 1000
    assert result['single_position_ratio'] == 1
    assert result['risk_budget_hint'] == 'strict'


def test_mixed_canadian_hkd_costs_require_verified_hkd_fx(research, monkeypatch):
    monkeypatch.setattr(accounts, '_hkd_rate_cache', {'rate': 12345, 'ts': 99999999999})
    context, constraints, data = research.build(add_other_market='HK')
    assert constraints['total_cost'] == 5900
    assert constraints['single_position_ratio'] == round(5000 / 5900, 4)
    assert set(research.calls) == {'CAD', 'HKD'}


def test_repeated_canadian_lots_fetch_fx_once_and_preserve_fractions(research):
    context, _, data = research.build(add_other_market=None)
    original = context.portfolio.accounts[0].positions[0]
    original.quantity = 1.25
    context.portfolio.accounts.append(AccountInfo(2, 'Second', 20, [
        PositionInfo(2, 'Second', 1, 'T', 'TELUS', MarketCode.TSX, 12, 0.5)]))
    from src.modules.research.portfolio_currency import build_portfolio_valuation
    result = build_portfolio_valuation(context.portfolio, data['signal_packs'], refresh=True)
    assert result['positions']['TSX:T']['native_cost'] == 18.5
    assert result['total_cost_cny'] == 92.5
    assert research.calls == ['CAD']
