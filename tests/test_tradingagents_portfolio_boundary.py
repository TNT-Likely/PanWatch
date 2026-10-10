"""Actual installed graph state and Portfolio Manager holding-state regressions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib import import_module
from threading import Lock
from types import MethodType, SimpleNamespace

import pytest

from src.modules.automation.tradingagents import upstream_compat as compat
from src.modules.automation.tradingagents.data_context import to_tradingagents_portfolio
from src.modules.automation.tradingagents.runtime_support import build_ta_llm_config
from tradingagents.graph import trading_graph as tg
from tradingagents.graph.propagation import Propagator

MODERN = not hasattr(tg.TradingAgentsGraph, '_get_provider_kwargs')


@pytest.fixture(autouse=True)
def offline_boundaries(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        pytest.fail('Boundary regression must not make external requests')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)


def portfolio(*lots):
    return SimpleNamespace(accounts=[SimpleNamespace(available_funds=50000, positions=[
        SimpleNamespace(symbol=symbol, market=market, quantity=quantity, cost_price=price)
        for symbol, market, quantity, price in lots
    ])])


def cfg(path):
    return build_ta_llm_config(SimpleNamespace(model='fixture', base_url='https://example.invalid/v1'), runtime_dir=str(path))


def pin_identity(monkeypatch):
    if MODERN:
        identity = import_module('tradingagents.extensions.ashare_identity')
        def lookup(exchange, code, *args):
            if exchange == {'600519': 'SS', '000001': 'SZ', '920001': 'BJ'}.get(code):
                return dict(exchange={'SS': 'SSE', 'SZ': 'SZSE', 'BJ': 'BSE'}[exchange],
                            canonical_symbol=code + '.' + exchange, chinese_short_name='Fixture company',
                            source={'provider': exchange, 'url': 'https://example.invalid/'})
        monkeypatch.setattr(identity, '_lookup', lookup)


def state_graph(path):
    bounded = compat.bounded_graph_class(tg.TradingAgentsGraph)
    graph = bounded.__new__(bounded)
    graph.config = cfg(path)
    graph._checkpointer_ctx = None
    graph.memory_log = tg.TradingMemoryLog(graph.config)
    graph.propagator = Propagator()
    graph.resolve_instrument_context = lambda *a, **k: 'Fixture exact identity'
    def run(self, ticker, trade_date, **kwargs):
        return self.create_run_state(ticker, trade_date, kwargs.get('asset_type', 'stock'), kwargs.get('portfolio')), 'TEST'
    graph._run_graph = MethodType(run, graph)
    return graph


@pytest.mark.parametrize('symbol,exchange', [('600519', 'SS'), ('000001', 'SZ'), ('920001', 'BJ')])
def test_actual_graph_propagation_recognizes_cn_holding(monkeypatch, tmp_path, symbol, exchange):
    pin_identity(monkeypatch)
    original = portfolio((symbol, SimpleNamespace(value='CN'), 100, 123))
    before = deepcopy(original)
    book = to_tradingagents_portfolio(original, preserve_markets=MODERN)
    fingerprint, serialized = book.fingerprint(), book.model_dump()
    state, _ = state_graph(tmp_path).propagate(symbol, '2026-10-09', portfolio=book)
    canonical = symbol + '.' + exchange if MODERN else symbol
    assert state['company_of_interest'] == canonical
    assert f'Current position in {canonical}: 100 units, average price 123.00' in state['portfolio_context']
    assert 'Cash available: 50,000.00' in state['portfolio_context']
    assert book.model_dump() == serialized and book.fingerprint() == fingerprint
    assert original == before


@pytest.mark.skipif(not MODERN, reason='market-tagged alias adaptation is modern-only')
def test_market_separation_alias_aggregation_cost_weights_and_immutable_input(monkeypatch, tmp_path):
    pin_identity(monkeypatch)
    original = portfolio(
        ('600519', 'HK', 999, 999), ('600519.SS', 'US', 888, 888),
        ('600519', 'CN', 100, 100), ('600519', 'CN', -50, 200),
        ('600519.SS', 'CN', 50, 300), ('600519.SZ', 'CN', 777, 777),
        ('600519', '', 666, 666), ('600519', None, 555, 555),
    )
    before = deepcopy(original)
    book = to_tradingagents_portfolio(original, preserve_markets=True)
    serialized, fingerprint = book.model_dump(), book.fingerprint()
    graph = state_graph(tmp_path)
    state, _ = graph.propagate('600519', '2026-10-09', portfolio=book)
    assert 'Current position in 600519.SS: 100 units, average price 175.00' in state['portfolio_context']
    for distinct in ('600519 999', '600519.SS 888', '600519.SZ 777', '600519 1,221'):
        assert distinct in state['portfolio_context']
    assert original == before and book.model_dump() == serialized and book.fingerprint() == fingerprint
    other_market = to_tradingagents_portfolio(portfolio(('600519', 'HK', 100, 123)), preserve_markets=True)
    same_values = to_tradingagents_portfolio(portfolio(('600519', 'CN', 100, 123)), preserve_markets=True)
    assert other_market.fingerprint() != same_values.fingerprint()
    for market in ('HK', 'US', '', None):
        unheld = to_tradingagents_portfolio(portfolio(('600519', market, 100, 123)), preserve_markets=True)
        result, _ = graph.propagate('600519', '2026-10-09', portfolio=unheld)
        assert 'No current position in 600519.SS' in result['portfolio_context']


@pytest.mark.skipif(not MODERN, reason='modern identity lifecycle only')
def test_direct_state_resolves_identity_before_adaptation_and_drops_stale_identity(monkeypatch, tmp_path):
    pin_identity(monkeypatch)
    graph = state_graph(tmp_path)
    book = to_tradingagents_portfolio(portfolio(('600519', 'CN', 100, 123)), preserve_markets=True)
    state = graph.create_run_state('600519', '2026-10-09', portfolio=book)
    assert 'Current position in 600519.SS: 100 units' in state['portfolio_context']
    graph._identity_lifecycle_ended = True
    monkeypatch.setattr(import_module('tradingagents.extensions.ashare_identity'), '_lookup', lambda *a: None)
    state = graph.create_run_state('600519.SS', '2026-10-09', portfolio=book)
    assert 'No current position in 600519.SS' in state['portfolio_context']


@pytest.mark.parametrize('empty', [None, SimpleNamespace(accounts=[]), portfolio()])
def test_actual_graph_preserves_missing_vs_empty_portfolio(monkeypatch, tmp_path, empty):
    pin_identity(monkeypatch)
    book = to_tradingagents_portfolio(empty, preserve_markets=MODERN)
    state, _ = state_graph(tmp_path).propagate('600519', '2026-10-09', portfolio=book)
    if empty is None or not empty.accounts:
        assert state['portfolio_context'] == ''
    else:
        assert 'No current position in' in state['portfolio_context']
        assert 'Cash available: 50,000.00' in state['portfolio_context']


def test_real_portfolio_manager_receives_holdings_for_concurrent_cn_graphs(monkeypatch, tmp_path):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from src.modules.automation.tradingagents.agent import TradingAgentsAgent
    from src.modules.automation.tradingagents import toolkit_adapter as adapter
    from src.platform.marketdata.collectors.kline_collector import KlineData

    pin_identity(monkeypatch)
    prompts, tool_results, clients = [], [], []
    lock = Lock()
    class DeterministicModel(BaseChatModel):
        @property
        def _llm_type(self):
            return 'offline-portfolio-regression'
        def bind_tools(self, tools, **kwargs):
            return self.bind(fixture_tools=[t.name for t in tools])
        def with_structured_output(self, *args, **kwargs):
            raise NotImplementedError
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen = [m for m in messages if isinstance(m, ToolMessage)]
            with lock:
                prompts.append('\n'.join(str(m.content) for m in messages))
                tool_results.extend(str(m.content) for m in seen)
            tools = kwargs.get('fixture_tools', [])
            calls = []
            if not seen and 'get_stock_data' in tools:
                # Modern tools inject their scoped ticker; legacy tools accept it.
                symbol = next((s for s in ('600519', '000001') if any(s in str(m.content) for m in messages)), None)
                args = dict(start_date='2026-10-01', end_date='2026-10-09')
                if not MODERN:
                    args['symbol'] = symbol
                calls = [dict(name='get_stock_data', args=args, id='stock')]
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content='' if calls else 'Rating: Hold\nOffline deterministic output.', tool_calls=calls))])
    def create_client(**kwargs):
        with lock:
            clients.append(kwargs)
        return SimpleNamespace(get_llm=lambda: DeterministicModel())
    factory = import_module('tradingagents.llm_clients.factory') if MODERN else tg
    monkeypatch.setattr(factory, 'create_llm_client', create_client)
    monkeypatch.setattr(tg.TradingAgentsGraph, 'resolve_instrument_context', lambda *a, **k: 'Fixture exact identity')
    monkeypatch.setattr(adapter, '_emit_toolkit_log', lambda *a, **k: None)
    original_state = tg.TradingAgentsGraph.create_run_state
    def run(item):
        symbol, timeout, quantity, price = item
        config = build_ta_llm_config(SimpleNamespace(model=f'fixture-{timeout}', base_url='https://example.invalid/v1'),
                                    selected_analysts=['market'], runtime_dir=str(tmp_path / symbol), llm_timeout_seconds=timeout)
        original = portfolio((symbol, 'CN', quantity, price))
        before = deepcopy(original)
        result = TradingAgentsAgent()._run_tradingagents_sync(
            ai_client=SimpleNamespace(api_key=None), symbol=symbol, market='CN', ta_config=config, progress_handler=None,
            panwatch_data={'stock': SimpleNamespace(symbol=symbol, market='CN', name='Fixture company'),
                           'klines': [KlineData(date='2026-10-09', open=10, high=11, low=9, close=10, volume=100)]},
            stock_metadata_context='Fixture market-matched identity', portfolio=original)
        assert original == before
        return result['final_state']
    cases = [('600519', 7, 100, 123), ('000001', 19, -50, 456)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(run, cases))
    for state, (symbol, _, quantity, price) in zip(states, cases):
        ticker = state['company_of_interest']
        assert ticker == (symbol + ('.SS' if symbol == '600519' else '.SZ') if MODERN else symbol)
        expected = f'Current position in {ticker}: {quantity} units, average price {price:.2f}'
        assert expected in state['portfolio_context']
        assert any('As the Portfolio Manager' in prompt and expected in prompt for prompt in prompts)
    assert any('2026-10-09,10,11,9,10,100' in result for result in tool_results)
    assert sorted((c['model'], c['timeout'], c['max_retries']) for c in clients) == [('fixture-19', 19.0, 0)] * 2 + [('fixture-7', 7.0, 0)] * 2
    assert tg.TradingAgentsGraph.create_run_state is original_state
    assert adapter._patch_refcount == 0


@pytest.mark.parametrize('symbol,market', [('AAPL', 'US'), ('00700.HK', 'HK')])
def test_other_markets_keep_their_symbols_and_aggregate_lots(monkeypatch, tmp_path, symbol, market):
    pin_identity(monkeypatch)
    book = to_tradingagents_portfolio(portfolio((symbol, market, 10, 100), (symbol, market, 30, 200)), preserve_markets=MODERN)
    original = book.model_dump()
    state, _ = state_graph(tmp_path).propagate(symbol, '2026-10-09', portfolio=book)
    assert f'Current position in {symbol}: 40 units, average price 175.00' in state['portfolio_context']
    assert book.model_dump() == original


@pytest.mark.skipif(not MODERN, reason='modern identity state boundary only')
def test_unknown_modern_portfolio_boundary_fails_availability(monkeypatch):
    monkeypatch.delattr(tg.TradingAgentsGraph, 'create_run_state')
    with pytest.raises(ValueError, match='portfolio state boundary'):
        compat.validate_upstream_contract()
