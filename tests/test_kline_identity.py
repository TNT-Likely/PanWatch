"""K-line routes must use the same canonical identities as quote routes."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.market.api import klines


@pytest.fixture
def client(monkeypatch):
    calls = []
    monkeypatch.setattr(klines.KlineCollector, 'get_klines',
                        lambda self, symbol, **kw: calls.append((self.market.value, symbol)) or [])
    monkeypatch.setattr(klines.KlineCollector, 'get_kline_summary',
                        lambda self, symbol: calls.append((self.market.value, symbol)) or {})
    app = FastAPI()
    app.include_router(klines.router, prefix='/klines')
    return TestClient(app), calls


@pytest.mark.parametrize('suffix', ['', '/summary'])
@pytest.mark.parametrize('symbol,market,canonical', [
    ('RY.TO', 'TSX', 'RY'), ('rck.v', 'TSXV', 'RCK'),
    ('BAM.A.TO', 'TSX', 'BAM-A'), ('BCE.PR.T', 'TSX', 'BCE-PT'),
    ('600519', 'CN', '600519'), ('00700', 'HK', '00700'), ('RY', 'US', 'RY'),
])
def test_single_canonical_identity(client, suffix, symbol, market, canonical):
    api, calls = client
    response = api.get(f'/klines/{symbol}{suffix}', params={'market': market})
    assert response.status_code == 200
    assert response.json()['symbol'] == symbol
    assert calls == [(market, canonical)]


@pytest.mark.parametrize('suffix', ['', '/summary'])
@pytest.mark.parametrize('symbol,market', [('RY.TO', 'US'), ('RY.TO', 'TSXV'), ('RCK.V', 'TSX')])
def test_single_rejects_conflicting_exchange(client, suffix, symbol, market):
    api, calls = client
    response = api.get(f'/klines/{symbol}{suffix}', params={'market': market})
    assert response.status_code == 400
    assert calls == []


@pytest.mark.parametrize('path', ['/batch', '/summary/batch'])
def test_batch_deduplicates_aliases_preserving_response_symbols(client, path):
    api, calls = client
    response = api.post('/klines' + path, json={'items': [
        {'symbol': symbol, 'market': 'TSX'} for symbol in ['RY.TO', 'RY', 'ry.to']
    ]})
    assert response.status_code == 200
    assert [row['symbol'] for row in response.json()] == ['RY.TO', 'RY', 'ry.to']
    assert calls == [('TSX', 'RY')]


@pytest.mark.parametrize('path', ['/batch', '/summary/batch'])
def test_batch_validates_every_item_before_fetch(client, path):
    api, calls = client
    response = api.post('/klines' + path, json={'items': [
        {'symbol': 'RY', 'market': 'TSX'}, {'symbol': 'RY.TO', 'market': 'US'},
    ]})
    assert response.status_code == 400
    assert calls == []
