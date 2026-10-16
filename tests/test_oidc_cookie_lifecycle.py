"""CookieJar response-order regressions through the production app assembly.

The provider is a disposable signed fixture. Cookie response application uses
httpx's real CookieJar; browser SameSite withholding is modeled explicitly.
"""
import importlib
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from starlette.responses import StreamingResponse

from test_oidc_auth import env, db, provider, ORIGIN, cookie_value
from src.modules.administration import oidc
from src.modules.administration.api.auth import get_current_user
from src.platform.persistence.database import get_db
from src.platform.persistence.models import OIDCLoginTransaction, OIDCSession


@pytest.fixture(params=[
    ('', 'preserve'),
    ('/panwatch', 'preserve'), ('/panwatch', 'strip'),
    ('/apps/panwatch', 'preserve'), ('/apps/panwatch', 'strip'),
], ids=['root', 'single-preserve', 'single-strip', 'nested-preserve', 'nested-strip'])
def assembly_client(request, db, monkeypatch):
    prefix, mode = request.param
    if prefix and importlib.util.find_spec('src.web.spa') is None:
        pytest.skip('Requires the independently reviewed reverse-proxy subpath feature')
    monkeypatch.setenv('PANWATCH_BASE_PATH', prefix)
    monkeypatch.setenv('PANWATCH_PROXY_MODE', mode)
    monkeypatch.setenv('OIDC_PUBLIC_URL', ORIGIN + prefix)
    oidc.get_config.cache_clear()
    monkeypatch.setattr(oidc, 'SessionLocal', sessionmaker(bind=db.bind))
    import src.bootstrap.application as assembly
    monkeypatch.setattr(assembly, 'app', assembly.app)
    monkeypatch.setattr(assembly, 'application_lifespan', assembly.application_lifespan)
    app = importlib.reload(assembly).app
    app.dependency_overrides[get_db] = lambda: db

    @app.get('/api/contract-stream')
    def contract_stream(user=Depends(get_current_user)):
        return StreamingResponse(iter([b'data: owner-event\n\n']), media_type='text/event-stream')

    async def proxy(scope, receive, send):
        if mode == 'strip' and scope['type'] == 'http':
            scope = dict(scope)
            assert scope['path'].startswith(prefix + '/')
            scope['path'] = scope['path'][len(prefix):]
            scope['raw_path'] = scope['raw_path'][len(prefix):]
        await app(scope, receive, send)

    client = TestClient(proxy, base_url=ORIGIN, follow_redirects=False)
    yield client, proxy, prefix
    client.close()


def start(client, prefix, provider):
    response = client.get(prefix + '/api/auth/oidc/login')
    assert response.status_code == 303
    assert f'Max-Age={oidc.TRANSACTION_SECONDS + oidc.get_config().session_seconds}' in response.headers['set-cookie']
    params = parse_qs(urlsplit(response.headers['location']).query)
    provider['nonce'], provider['challenge'] = params['nonce'][0], params['code_challenge'][0]
    return params['state'][0]


def complete(client, prefix, provider):
    state = start(client, prefix, provider)
    response = client.get(prefix + '/api/auth/oidc/callback', params={'state': state, 'code': 'signed-fixture-code'})
    assert response.headers['location'] == ORIGIN + prefix + '/'
    return response


def status(client, prefix):
    return client.get(prefix + '/api/auth/status').json()['data']


def test_cross_site_logout_without_cookie_has_no_side_effects(assembly_client, db, provider):
    client, app, prefix = assembly_client
    complete(client, prefix, provider)
    config = oidc.get_config()
    original_cookie = cookie_value(client, config.session_cookie)
    # A Lax cookie is withheld on this unsafe cross-site top-level navigation;
    # its response can still mutate the browser's cookies before JS sees it.
    navigation = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    response = navigation.post(prefix + '/api/auth/logout', headers={
        'Origin': 'https://other.example', 'Sec-Fetch-Site': 'cross-site',
        'Sec-Fetch-Mode': 'navigate',
    })
    navigation.close()
    assert not response.request.headers.get('cookie')
    assert response.status_code == 200
    assert 'access-control-allow-origin' not in response.headers
    assert 'set-cookie' not in response.headers
    assert response.headers['cache-control'] == 'no-store'
    client.cookies.extract_cookies(response)
    assert cookie_value(client, config.session_cookie) == original_cookie
    assert status(client, prefix)['authenticated']
    assert db.query(OIDCSession).filter_by(session_hash=oidc.digest(original_cookie)).count() == 1


def test_delayed_logout_response_preserves_new_session(assembly_client, db, provider):
    client, app, prefix = assembly_client
    complete(client, prefix, provider)
    config = oidc.get_config()
    old_cookie = cookie_value(client, config.session_cookie)
    csrf = status(client, prefix)['csrf_token']
    old_request = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    old_request.cookies.update(client.cookies)
    delayed_response = old_request.post(prefix + '/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf})
    old_request.close()
    assert delayed_response.status_code == 200
    assert 'set-cookie' not in delayed_response.headers
    assert not db.query(OIDCSession).filter_by(session_hash=oidc.digest(old_cookie)).count()
    complete(client, prefix, provider)
    new_cookie = cookie_value(client, config.session_cookie)
    assert new_cookie != old_cookie
    client.cookies.extract_cookies(delayed_response)
    assert cookie_value(client, config.session_cookie) == new_cookie
    assert status(client, prefix)['authenticated']
    assert db.query(OIDCSession).filter_by(session_hash=oidc.digest(new_cookie)).count() == 1
    assert client.get(prefix + '/api/contract-stream').text == 'data: owner-event\n\n'


@pytest.mark.parametrize('obsolete_params', [{'code': 'obsolete-code'}, {'error': 'access_denied'}])
def test_obsolete_callback_preserves_current_login(assembly_client, db, provider, obsolete_params):
    client, _, prefix = assembly_client
    state_a = start(client, prefix, provider)
    state_b = start(client, prefix, provider)
    config = oidc.get_config()
    current_binding = cookie_value(client, config.transaction_cookie)
    response_a = client.get(prefix + '/api/auth/oidc/callback', params={'state': state_a, **obsolete_params})
    assert response_a.headers['location'].endswith('oidc_error=failed')
    assert 'set-cookie' not in response_a.headers
    assert cookie_value(client, config.transaction_cookie) == current_binding
    assert db.query(OIDCLoginTransaction).filter_by(state_hash=oidc.digest(state_b)).count() == 1
    response_b = client.get(prefix + '/api/auth/oidc/callback', params={'state': state_b, 'code': 'current-code'})
    assert response_b.headers['location'] == ORIGIN + prefix + '/'
    assert status(client, prefix)['authenticated']
    assert db.query(OIDCSession).count() == 1


@pytest.mark.parametrize('outcome', ['success', 'cancelled', 'failed', 'denied'])
def test_delayed_bound_callback_cannot_erase_new_transaction(assembly_client, db, provider, outcome):
    client, app, prefix = assembly_client
    config = oidc.get_config()
    state_a = start(client, prefix, provider)
    binding_a = cookie_value(client, config.transaction_cookie)
    old_request = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    old_request.cookies.update(client.cookies)
    params = {'state': state_a, 'code': 'signed-fixture-code'}
    if outcome == 'cancelled':
        params = {'state': state_a, 'error': 'access_denied'}
    elif outcome == 'failed':
        provider['claims']['nonce'] = 'invalid-nonce'
    elif outcome == 'denied':
        provider['claims']['sub'] = 'not-the-owner'
    delayed_response = old_request.get(prefix + '/api/auth/oidc/callback', params=params)
    expected = ORIGIN + prefix + ('/' if outcome == 'success' else '/login?oidc_error=' + outcome)
    assert delayed_response.headers['location'] == expected
    assert cookie_value(old_request, config.transaction_cookie) == binding_a
    assert not db.query(OIDCLoginTransaction).filter_by(state_hash=oidc.digest(state_a)).count()
    # Retaining an opaque cookie does not make a consumed transaction reusable.
    calls_before_replay = len(provider['calls'])
    replay = old_request.get(prefix + '/api/auth/oidc/callback', params={'state': state_a, 'code': 'replayed-code'})
    assert replay.headers['location'].endswith('oidc_error=failed')
    assert 'set-cookie' not in replay.headers
    assert len(provider['calls']) == calls_before_replay
    old_request.close()
    provider['claims'].clear()
    state_b = start(client, prefix, provider)
    binding_b = cookie_value(client, config.transaction_cookie)
    assert binding_b != binding_a
    client.cookies.extract_cookies(delayed_response)
    assert cookie_value(client, config.transaction_cookie) == binding_b
    response_b = client.get(prefix + '/api/auth/oidc/callback', params={'state': state_b, 'code': 'current-code'})
    assert response_b.headers['location'] == ORIGIN + prefix + '/'
    assert status(client, prefix)['authenticated']
    assert db.query(OIDCSession).count() == 1


@pytest.mark.parametrize('invalidation', ['logout', 'expire', 'revoke'])
def test_retained_invalid_cookie_cannot_authenticate_or_resume_sse(assembly_client, db, provider, invalidation):
    client, _, prefix = assembly_client
    response = complete(client, prefix, provider)
    config = oidc.get_config()
    cookie = cookie_value(client, config.session_cookie)
    assert f'Max-Age={config.session_seconds}' in response.headers['set-cookie']
    csrf = status(client, prefix)['csrf_token']
    assert client.get(prefix + '/api/contract-stream').text == 'data: owner-event\n\n'
    if invalidation == 'logout':
        result = client.post(prefix + '/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf})
        assert result.status_code == 200 and 'set-cookie' not in result.headers
    else:
        sessions = db.query(OIDCSession).filter_by(session_hash=oidc.digest(cookie))
        if invalidation == 'expire':
            sessions.update({'expires_at': 1})
        else:
            sessions.delete()
        db.commit()
    assert cookie_value(client, config.session_cookie) == cookie
    assert not status(client, prefix)['authenticated']
    for path in ('/api/accounts', '/api/contract-stream'):
        assert client.get(prefix + path).status_code == 401
    # Repeated logout of stale state is inert, including a foreign Origin.
    repeated = client.post(prefix + '/api/auth/logout', headers={'Origin': 'https://other.example'})
    assert repeated.status_code == 200 and 'set-cookie' not in repeated.headers
    assert cookie_value(client, config.session_cookie) == cookie
    # A fresh sign-in replaces the inert identifier and can be logged out again.
    complete(client, prefix, provider)
    assert cookie_value(client, config.session_cookie) != cookie
    current = status(client, prefix)
    assert current['authenticated']
    assert client.get(prefix + '/api/contract-stream').text == 'data: owner-event\n\n'
    assert client.post(prefix + '/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': current['csrf_token']}).status_code == 200
    assert not status(client, prefix)['authenticated']
    assert db.query(OIDCSession).count() == 0


def test_expired_transaction_cookie_cannot_finish_login(assembly_client, db, provider):
    client, _, prefix = assembly_client
    state = start(client, prefix, provider)
    config = oidc.get_config()
    binding = cookie_value(client, config.transaction_cookie)
    db.query(OIDCLoginTransaction).update({'expires_at': 1})
    db.commit()
    calls_before = len(provider['calls'])
    response = client.get(prefix + '/api/auth/oidc/callback', params={'state': state, 'code': 'expired-code'})
    assert response.headers['location'].endswith('oidc_error=failed')
    assert 'set-cookie' not in response.headers
    assert cookie_value(client, config.transaction_cookie) == binding
    assert len(provider['calls']) == calls_before
    assert db.query(OIDCSession).count() == 0
    assert not status(client, prefix)['authenticated']
    complete(client, prefix, provider)
    assert status(client, prefix)['authenticated']
    assert db.query(OIDCLoginTransaction).count() == 0
