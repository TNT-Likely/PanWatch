"""OIDC protocol tests use a disposable signed provider fixture, no real network."""
import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.administration import oidc
from src.modules.administration.api import auth, health
from src.modules.administration.api import oidc as oidc_api
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import OIDCLoginTransaction, OIDCSession
from src.web.response import ResponseWrapperMiddleware
from src.web.auth_headers import AuthResponseHeadersMiddleware
from src.web.oidc_streams import OIDCStreamSessionMiddleware

ORIGIN = 'https://portfolio.example'
ISSUER = 'https://identity.example/tenant'


@pytest.fixture
def env(monkeypatch):
    for name in list(os.environ):
        if name.startswith('OIDC_') or name == 'PANWATCH_BASE_PATH':
            monkeypatch.delenv(name)
    values = {'OIDC_ENABLED': 'true', 'OIDC_ISSUER': ISSUER, 'OIDC_CLIENT_ID': 'panwatch', 'OIDC_CLIENT_SECRET': 'disposable-test-fixture', 'OIDC_PUBLIC_URL': ORIGIN, 'OIDC_ALLOWED_SUBJECTS': '["owner"]'}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    oidc.get_config.cache_clear()
    monkeypatch.setattr(auth, 'get_jwt_secret', lambda: 'disposable-test-secret-not-used-outside-tests-0123456789')
    yield
    oidc.get_config.cache_clear()


@pytest.fixture
def db(env):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def app(db, monkeypatch):
    app = FastAPI()
    app.add_middleware(ResponseWrapperMiddleware)
    app.add_middleware(AuthResponseHeadersMiddleware)
    app.add_middleware(OIDCStreamSessionMiddleware)
    monkeypatch.setattr(oidc, "SessionLocal", sessionmaker(bind=db.bind))
    app.include_router(auth.router, prefix='/api/auth')
    app.include_router(oidc_api.router, prefix='/api/auth/oidc')
    app.include_router(health.router, prefix='/api/health', dependencies=[Depends(auth.get_current_user)])
    app.dependency_overrides[get_db] = lambda: db

    @app.api_route('/api/private', methods=['GET', 'POST', 'PATCH', 'DELETE'])
    def private(user=Depends(auth.get_current_user)):
        return {'user': user}

    @app.post('/api/stream')
    def stream(user=Depends(auth.get_current_user)):
        from starlette.responses import StreamingResponse
        return StreamingResponse(iter([b'data: ok\n\n']), media_type='text/event-stream')

    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        yield client


@pytest.fixture
def provider(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update(kid='test-key', alg='RS256', use='sig')
    metadata = {'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/authorize', 'token_endpoint': ISSUER + '/token', 'jwks_uri': ISSUER + '/jwks', 'response_types_supported': ['code'], 'code_challenge_methods_supported': ['S256'], 'token_endpoint_auth_methods_supported': ['client_secret_basic', 'client_secret_post', 'none'], 'id_token_signing_alg_values_supported': ['RS256']}
    state = {'metadata': metadata, 'claims': {}, 'calls': [], 'key': private_key, 'jwk': jwk, 'status': 200}

    def handle(request):
        state['calls'].append(request)
        if state['status'] != 200:
            return httpx.Response(state['status'], headers={'location': 'https://untrusted.example'}, json={'error': 'test_failure'})
        if request.url.path.endswith('openid-configuration'):
            return httpx.Response(200, json=metadata)
        if request.url.path.endswith('jwks'):
            return httpx.Response(200, json={'keys': [state['jwk']]})
        if request.url.path.endswith('token'):
            params = parse_qs(request.content.decode())
            assert params['grant_type'] == ['authorization_code']
            assert params['redirect_uri'] == [oidc.get_config().callback_url]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(params['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
            assert challenge == state['challenge']
            claims = {'iss': ISSUER, 'aud': 'panwatch', 'sub': 'owner', 'iat': int(time.time()), 'exp': int(time.time()) + 600, 'nonce': state['nonce'], **state['claims']}
            for key in state.get('omit', []):
                claims.pop(key, None)
            token = jwt.encode(claims, state['key'], algorithm='RS256', headers={'kid': 'test-key'})
            return httpx.Response(200, json={'id_token': token, 'access_token': 'must-never-be-persisted', 'token_type': 'Bearer'})
        raise AssertionError(request.url)

    monkeypatch.setattr(oidc, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    return state


def cookie_name(client, base):
    """Most recent exact-namespace generation cookie in this browser fixture."""
    import re
    names = [cookie.name for cookie in client.cookies.jar
             if re.fullmatch(re.escape(base) + r"_[0-9a-f]{64}", cookie.name)]
    assert names, (base, list(client.cookies.keys()))
    return names[-1]


def cookie_value(client, base):
    return client.cookies[cookie_name(client, base)]


def start(client, provider):
    response = client.get('/api/auth/oidc/login')
    assert response.status_code == 303
    params = parse_qs(urlsplit(response.headers['location']).query)
    provider['nonce'], provider['challenge'] = params['nonce'][0], params['code_challenge'][0]
    assert params['scope'] == ['openid']
    assert params['code_challenge_method'] == ['S256']
    assert params['response_type'] == ['code']
    return params['state'][0], response


def complete(client, provider):
    state, _ = start(client, provider)
    return client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'one-use-code'})


def data(response):
    return response.json()['data']


def test_code_flow_cookie_session_csrf_and_logout(client, provider, db):
    assert not data(client.get('/api/auth/status'))['authenticated']
    assert client.get('/api/private').status_code == 401
    state, started = start(client, provider)
    cookie = started.headers['set-cookie']
    assert all(flag in cookie for flag in ['HttpOnly', 'Secure', 'SameSite=lax'])
    response = client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'one-use-code'})
    assert response.status_code == 303 and response.headers['location'] == ORIGIN + '/'
    assert response.headers['cache-control'] == 'no-store'
    assert 'must-never-be-persisted' not in response.text + str(response.headers)
    assert db.query(OIDCSession).one().session_hash != cookie_value(client, oidc.SESSION_COOKIE)
    status = data(client.get('/api/auth/status'))
    assert status['authenticated'] and status['auth_method'] == 'oidc' and not status['password_enabled']
    assert client.get('/api/private').status_code == 200
    assert client.post('/api/private').status_code == 403
    assert client.post('/api/private', headers={'X-CSRF-Token': status['csrf_token'], 'Origin': 'https://evil.example'}).status_code == 403
    headers = {'X-CSRF-Token': status['csrf_token'], 'Origin': ORIGIN}
    for method in (client.post, client.patch, client.delete):
        assert method('/api/private', headers=headers).status_code == 200
    assert client.post('/api/stream', headers=headers).text == 'data: ok\n\n'
    assert client.post('/api/stream').status_code == 403
    assert client.get('/api/auth/logout').status_code == 405
    assert client.post('/api/auth/logout').status_code == 403
    old_cookie = cookie_value(client, oidc.SESSION_COOKIE)
    assert client.post('/api/auth/logout', headers=headers).status_code == 200
    assert db.query(OIDCSession).count() == 0
    client.cookies.set(cookie_name(client, oidc.SESSION_COOKIE), old_cookie)
    assert client.get('/api/private').status_code == 401
    assert client.post('/api/auth/logout').status_code == 200


def test_wrong_browser_state_and_replay(client, provider, app, db):
    state, _ = start(client, provider)
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as other:
        response = other.get('/api/auth/oidc/callback', params={'state': state, 'code': 'stolen'})
        assert response.headers['location'].endswith('oidc_error=failed')
    assert not any(call.url.path.endswith('token') for call in provider['calls'])
    assert client.get('/api/auth/oidc/callback', params={'state': 'wrong', 'code': 'stolen'}).headers['location'].endswith('oidc_error=failed')
    state, _ = start(client, provider)
    assert client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'ok'}).headers['location'] == ORIGIN + '/'
    count = len([call for call in provider['calls'] if call.url.path.endswith('token')])
    assert client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'ok'}).headers['location'].endswith('oidc_error=failed')
    assert len([call for call in provider['calls'] if call.url.path.endswith('token')]) == count


@pytest.mark.parametrize('claim,value', [('iss', 'https://evil.example'), ('aud', 'another-client'), ('exp', 1), ('iat', 9999999999), ('nonce', 'wrong'), ('azp', 'other-client'), ('sub', 'other-owner'), ('sub', 123)])
def test_bad_id_token_claims(client, provider, db, claim, value):
    provider['claims'][claim] = value
    assert 'oidc_error=' in complete(client, provider).headers['location']
    assert db.query(OIDCSession).count() == 0
    assert client.get('/api/private').status_code == 401


@pytest.mark.parametrize('missing', ['iss', 'sub', 'aud', 'exp', 'iat', 'nonce'])
def test_required_id_token_claims(client, provider, missing):
    provider['omit'] = [missing]
    assert complete(client, provider).headers['location'].endswith('oidc_error=failed')


def test_multi_audience_and_signature(client, provider):
    provider['claims']['aud'] = ['panwatch', 'another-client']
    assert complete(client, provider).headers['location'].endswith('oidc_error=failed')
    provider['claims']['azp'] = 'panwatch'
    assert complete(client, provider).headers['location'] == ORIGIN + '/'
    provider['key'] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert complete(client, provider).headers['location'].endswith('oidc_error=failed')


@pytest.mark.parametrize('metadata,value', [('issuer', 'https://evil.example'), ('token_endpoint', 'http://192.168.1.2/token'), ('jwks_uri', 'https://user:pass@example.com/keys'), ('code_challenge_methods_supported', ['plain']), ('response_types_supported', ['token'])])
def test_discovery_fails_closed(client, provider, metadata, value):
    provider['metadata'][metadata] = value
    assert client.get('/api/auth/oidc/login').headers['location'].endswith('oidc_error=unavailable')


@pytest.mark.parametrize('status', [302, 500, 503])
def test_provider_failure_and_no_redirect_following(client, provider, status):
    provider['status'] = status
    assert client.get('/api/auth/oidc/login').headers['location'] == ORIGIN + '/login?oidc_error=unavailable'
    assert len(provider['calls']) == 1


def test_cancel_and_expired_transaction(client, provider, db):
    state, _ = start(client, provider)
    result = client.get('/api/auth/oidc/callback', params={'state': state, 'error': 'access_denied', 'error_description': 'private-provider-information'})
    assert result.headers['location'].endswith('oidc_error=cancelled')
    assert 'private-provider-information' not in result.text + str(result.headers)
    assert db.query(OIDCLoginTransaction).count() == 0
    state, _ = start(client, provider)
    db.query(OIDCLoginTransaction).update({'expires_at': 1})
    db.commit()
    assert client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'code'}).headers['location'].endswith('oidc_error=failed')


def test_sso_only_blocks_guest_setup_password_and_legacy_jwt(client, db):
    token, _ = auth.create_token()
    assert client.get('/api/private', headers={'Authorization': 'Bearer ' + token}).status_code == 401
    assert client.post('/api/auth/setup', json={'username': 'me', 'password': 'example'}).status_code == 403
    auth.set_stored_username(db, 'owner')
    auth.set_password_hash(db, auth.hash_password('example'))
    assert client.get('/api/private', headers={'Authorization': 'Bearer ' + token}).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'owner', 'password': 'example'}).status_code == 403
    assert not data(client.get('/api/auth/status', headers={'Authorization': 'Bearer ' + token}))['authenticated']


def test_explicit_password_fallback(client, db, monkeypatch):
    monkeypatch.setenv('OIDC_ALLOW_PASSWORD_LOGIN', 'true')
    oidc.get_config.cache_clear()
    assert client.get('/api/private').status_code == 401
    assert client.post('/api/auth/setup', json={'username': 'me', 'password': 'example'}).status_code == 403
    auth.set_stored_username(db, 'owner')
    auth.set_password_hash(db, auth.hash_password('example'))
    token = data(client.post('/api/auth/login', json={'username': 'owner', 'password': 'example'}))['token']
    headers = {'Authorization': 'Bearer ' + token}
    assert client.post('/api/private', headers=headers).status_code == 200
    assert data(client.get('/api/auth/status', headers=headers))['auth_method'] == 'password'


def test_disabled_preserves_password_setup(client, monkeypatch):
    monkeypatch.setenv('OIDC_ENABLED', 'false')
    oidc.get_config.cache_clear()
    assert client.get('/api/private').status_code == 200
    status = data(client.get('/api/auth/status'))
    assert not status['initialized'] and not status['oidc_enabled'] and status['password_enabled']
    token = data(client.post('/api/auth/setup', json={'username': 'owner', 'password': 'example'}))['token']
    assert client.get('/api/private').status_code == 401
    assert client.get('/api/private', headers={'Authorization': 'Bearer ' + token}).status_code == 200
    assert client.get('/api/auth/oidc/login').status_code == 404


@pytest.mark.parametrize('key,value', [('OIDC_ENABLED', 'typo'), ('OIDC_ALLOWED_SUBJECTS', '[]'), ('OIDC_ALLOWED_SUBJECTS', '[""]'), ('OIDC_ALLOWED_SUBJECTS', '"owner"'), ('OIDC_ISSUER', 'http://identity.example'), ('OIDC_PUBLIC_URL', 'https://portfolio.example/prefix'), ('OIDC_CLIENT_ID', ''), ('OIDC_CLIENT_SECRET', ''), ('OIDC_SESSION_HOURS', '999'), ('OIDC_ALLOW_PASSWORD_LOGIN', 'typo')])
def test_invalid_config_never_bypasses_auth(client, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    oidc.get_config.cache_clear()
    assert client.get('/api/private').status_code == 503
    assert client.get('/api/auth/status').status_code == 503


def test_callback_prefix_cookie_path_ignore_forwarded_hosts(client, provider, monkeypatch):
    monkeypatch.setenv('PANWATCH_BASE_PATH', '/apps/panwatch')
    monkeypatch.setenv('OIDC_PUBLIC_URL', ORIGIN + '/apps/panwatch')
    oidc.get_config.cache_clear()
    result = client.get('/api/auth/oidc/login', headers={'Host': 'evil.example', 'X-Forwarded-Host': 'evil.example', 'X-Forwarded-Prefix': '/evil'})
    params = parse_qs(urlsplit(result.headers['location']).query)
    assert params['redirect_uri'] == [ORIGIN + '/apps/panwatch/api/auth/oidc/callback']
    assert 'Path=/apps/panwatch/' in result.headers['set-cookie']
    assert result.headers['set-cookie'].startswith(oidc.get_config().transaction_cookie + '_')
    assert '/evil' not in oidc.get_config().cookie_path


def test_session_persistence_expiry_and_owner_removal(client, app, provider, db, monkeypatch):
    assert complete(client, provider).headers['location'] == ORIGIN + '/'
    with TestClient(app, base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get('/api/private').status_code == 200
        monkeypatch.setenv('OIDC_ALLOWED_SUBJECTS', '["different-owner"]')
        oidc.get_config.cache_clear()
        assert restarted.get('/api/private').status_code == 401
        monkeypatch.setenv('OIDC_ALLOWED_SUBJECTS', '["owner"]')
        oidc.get_config.cache_clear()
        db.query(OIDCSession).update({'expires_at': 1})
        db.commit()
        assert restarted.get('/api/private').status_code == 401


def test_login_rotates_and_revokes_previous_session(client, provider, app, db):
    complete(client, provider)
    old_name = cookie_name(client, oidc.SESSION_COOKIE)
    old = cookie_value(client, oidc.SESSION_COOKIE)
    complete(client, provider)
    assert old != cookie_value(client, oidc.SESSION_COOKIE)
    assert db.query(OIDCSession).count() == 1
    with TestClient(app, base_url=ORIGIN) as other:
        other.cookies.set(old_name, old)
        assert other.get('/api/private').status_code == 401


def test_verified_email_does_not_auto_authorize(client, provider):
    provider['claims'].update(sub='unapproved-subject', email='owner@example.com', email_verified=True)
    assert complete(client, provider).headers['location'].endswith('oidc_error=denied')


def test_auth_responses_are_never_cached(client):
    for response in (client.get('/api/auth/status'), client.post('/api/auth/setup', json={'username': 'me', 'password': 'example'}), client.get('/api/auth/oidc/callback')):
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['referrer-policy'] == 'no-referrer'


def test_discovery_cross_origin_requires_explicit_allowlist(client, provider, monkeypatch):
    provider['metadata']['jwks_uri'] = 'https://keys.identity.example/jwks'
    assert client.get('/api/auth/oidc/login').headers['location'].endswith('oidc_error=unavailable')
    monkeypatch.setenv('OIDC_ALLOWED_ENDPOINT_ORIGINS', '["https://keys.identity.example"]')
    oidc.get_config.cache_clear()
    assert complete(client, provider).headers['location'] == ORIGIN + '/'


def test_client_authentication_methods_and_secret_not_in_authorization_url(client, provider, monkeypatch):
    for method in ('client_secret_basic', 'client_secret_post', 'none'):
        monkeypatch.setenv('OIDC_TOKEN_AUTH_METHOD', method)
        oidc.get_config.cache_clear()
        assert complete(client, provider).headers['location'] == ORIGIN + '/'
        request = next(call for call in reversed(provider['calls']) if call.url.path.endswith('token'))
        form = parse_qs(request.content.decode())
        if method == 'client_secret_basic':
            assert request.headers['authorization'].startswith('Basic ')
            assert 'client_secret' not in form
        elif method == 'client_secret_post':
            assert form['client_secret'] == ['disposable-test-fixture']
            assert 'authorization' not in request.headers
        else:
            assert form['client_id'] == ['panwatch'] and 'client_secret' not in form
            assert 'authorization' not in request.headers
    assert 'disposable-test-fixture' not in repr(oidc.get_config())


def test_logging_redacts_callback_and_provider_diagnostics_before_all_sinks(client, provider, caplog):
    import logging
    from src.platform.observability.log_context import install_log_record_factory
    install_log_record_factory()
    caplog.set_level(logging.DEBUG)
    # Uvicorn logs access after the route's ContextVar scope has ended.
    logging.getLogger('uvicorn.access').info('%s - "%s %s HTTP/%s" %d', '127.0.0.1', 'GET', '/prefix/api/auth/oidc/callback?code=CODE_CANARY&state=STATE_CANARY', '1.1', 303)
    logging.getLogger('uvicorn.access').info('%s - "%s %s HTTP/%s" %d', '127.0.0.1', 'GET', '/prefix%2Fapi%2Fauth%2Foidc%2Fcallback?code=ENCODED_CANARY', '1.1', 303)
    original = oidc.http_client
    class LoggedClient:
        def __enter__(self):
            logging.getLogger('httpcore.http11').debug("receive_response_headers.complete return_value=%s", {'Set-Cookie': 'COOKIE_CANARY', 'secret': 'SECRET_CANARY'})
            self.client = original().__enter__()
            return self.client
        def __exit__(self, *args):
            return self.client.__exit__(*args)
    from unittest.mock import patch
    with patch.object(oidc, 'http_client', LoggedClient):
        assert complete(client, provider).headers['location'] == ORIGIN + '/'
    text = '\n'.join(record.getMessage() + repr(record.args) for record in caplog.records)
    for canary in ('CODE_CANARY', 'STATE_CANARY', 'ENCODED_CANARY', 'COOKIE_CANARY', 'SECRET_CANARY'):
        assert canary not in text
    assert 'details redacted' in text


@pytest.mark.parametrize('action', ['revoke', 'expire'])
def test_live_sse_stops_delivering_after_session_revocation_or_expiry(client, app, provider, db, action):
    from starlette.responses import StreamingResponse
    @app.get('/api/revocation-stream')
    def revocation_stream(user=Depends(auth.get_current_user)):
        async def events():
            yield b'data: before\n\n'
            if action == 'revoke':
                db.query(OIDCSession).delete()
            else:
                db.query(OIDCSession).update({'expires_at': 1})
            db.commit()
            yield b'data: AFTER_REVOKE_CANARY\n\n'
        return StreamingResponse(events(), media_type='text/event-stream')
    complete(client, provider)
    result = client.get('/api/revocation-stream')
    assert 'data: before' in result.text
    assert 'AFTER_REVOKE_CANARY' not in result.text
    assert cookie_value(client, oidc.get_config().session_cookie)
    assert client.get('/api/private').status_code == 401
    assert client.get('/api/revocation-stream').status_code == 401


@pytest.mark.parametrize('public_url', ['https://Portfolio.Example:443', 'https://portfolio.example:443/'])
def test_canonical_public_origin_accepts_browser_csrf_origin(client, provider, monkeypatch, public_url):
    monkeypatch.setenv('OIDC_PUBLIC_URL', public_url)
    oidc.get_config.cache_clear()
    assert oidc.get_config().public_url == ORIGIN
    assert complete(client, provider).headers['location'] == ORIGIN + '/'
    status = data(client.get('/api/auth/status'))
    assert client.post('/api/private', headers={'Origin': ORIGIN, 'X-CSRF-Token': status['csrf_token']}).status_code == 200


@pytest.mark.parametrize('public_url', ['https://127.1', 'https://2130706433', 'https://0177.0.0.1', 'https://0x7f000001', 'https://ex%61mple.com'])
def test_ambiguous_browser_hosts_are_rejected(client, monkeypatch, public_url):
    monkeypatch.setenv('OIDC_PUBLIC_URL', public_url)
    oidc.get_config.cache_clear()
    assert client.get('/api/auth/status').status_code == 503


def test_ipv6_browser_origin_is_canonical(env, monkeypatch):
    monkeypatch.setenv('OIDC_PUBLIC_URL', 'https://[0:0:0:0:0:0:0:1]:443')
    oidc.get_config.cache_clear()
    assert oidc.get_config().origin == 'https://[::1]'


def test_cookie_authenticated_data_and_downloads_are_not_cacheable(client, app, provider):
    from starlette.responses import Response
    @app.get('/api/private.pdf')
    def private_pdf(user=Depends(auth.get_current_user)):
        return Response(b'test PDF content', media_type='application/pdf')
    complete(client, provider)
    for path in ('/api/private', '/api/private.pdf'):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert set(response.headers['vary'].split(', ')) >= {'Cookie', 'Authorization'}


def nested_client(app, prefix):
    # Emulate a trusted prefix-stripping proxy without importing the independent
    # subpath-routing feature; the browser still enforces the external cookie path.
    async def proxy(scope, receive, send):
        if scope['type'] == 'http':
            scope = dict(scope)
            scope['path'] = scope['path'][len(prefix):]
            scope['raw_path'] = scope['raw_path'][len(prefix):]
        await app(scope, receive, send)
    return TestClient(proxy, base_url=ORIGIN + prefix + '/', follow_redirects=False)


@pytest.mark.parametrize('ancestor_path', ['/', '/apps/'])
@pytest.mark.parametrize('cookie_kind', ['session', 'transaction'])
def test_nested_login_ignores_ancestor_cookies(app, db, provider, monkeypatch, ancestor_path, cookie_kind):
    prefix = '/apps/panwatch'
    monkeypatch.setenv('PANWATCH_BASE_PATH', prefix)
    monkeypatch.setenv('OIDC_PUBLIC_URL', ORIGIN + prefix)
    oidc.get_config.cache_clear()
    config = oidc.get_config()
    with nested_client(app, prefix) as nested:
        legacy_name = oidc.SESSION_COOKIE if cookie_kind == 'session' else oidc.TRANSACTION_COOKIE
        nested.cookies.set(legacy_name, 'ancestor-cookie', domain='portfolio.example', path=ancestor_path)
        started = nested.get('api/auth/oidc/login')
        params = parse_qs(urlsplit(started.headers['location']).query)
        provider['nonce'], provider['challenge'] = params['nonce'][0], params['code_challenge'][0]
        assert started.headers['set-cookie'].startswith(config.transaction_cookie + '_')
        callback = nested.get('api/auth/oidc/callback', params={'state': params['state'][0], 'code': 'mock-code'})
        assert callback.headers['location'] == ORIGIN + prefix + '/'
        assert cookie_value(nested, config.transaction_cookie)
        assert db.query(OIDCLoginTransaction).count() == 0
        status = data(nested.get('api/auth/status'))
        assert status['authenticated']
        old = cookie_value(nested, config.session_cookie)
        headers = {'Origin': ORIGIN, 'X-CSRF-Token': status['csrf_token']}
        assert nested.post('api/auth/logout', headers=headers).status_code == 200
        assert cookie_value(nested, config.session_cookie) == old
        assert not data(nested.get('api/auth/status'))['authenticated']
        assert nested.cookies[legacy_name] == 'ancestor-cookie'
        assert not db.query(OIDCSession).filter_by(session_hash=oidc.digest(old)).count()


def test_authenticated_root_to_subpath_migration(app, client, db, provider, monkeypatch):
    complete(client, provider)
    root_cookie = cookie_value(client, oidc.SESSION_COOKIE)
    assert data(client.get('/api/auth/status'))['authenticated']
    prefix = '/apps/panwatch'
    monkeypatch.setenv('PANWATCH_BASE_PATH', prefix)
    monkeypatch.setenv('OIDC_PUBLIC_URL', ORIGIN + prefix)
    oidc.get_config.cache_clear()
    config = oidc.get_config()
    with nested_client(app, prefix) as nested:
        nested.cookies.update(client.cookies)
        for _ in range(2):
            started = nested.get('api/auth/oidc/login')
            params = parse_qs(urlsplit(started.headers['location']).query)
            provider['nonce'], provider['challenge'] = params['nonce'][0], params['code_challenge'][0]
            callback = nested.get('api/auth/oidc/callback', params={'state': params['state'][0], 'code': 'mock-code'})
            assert callback.headers['location'] == ORIGIN + prefix + '/'
            assert data(nested.get('api/auth/status'))['authenticated']
        assert config.session_cookie != oidc.SESSION_COOKIE
        assert cookie_value(nested, oidc.SESSION_COOKIE) == root_cookie
        # Root cookie is ignored; relogin revokes only the prior nested session.
        assert db.query(OIDCSession).filter_by(config_binding=config.binding).count() == 1
        assert db.query(OIDCSession).filter_by(session_hash=oidc.digest(root_cookie)).count() == 1


def test_nested_transaction_rotation_and_cancel_revoke_only_own_state(app, db, provider, monkeypatch):
    prefix = '/apps/panwatch'
    monkeypatch.setenv('PANWATCH_BASE_PATH', prefix)
    monkeypatch.setenv('OIDC_PUBLIC_URL', ORIGIN + prefix)
    oidc.get_config.cache_clear()
    config = oidc.get_config()
    with nested_client(app, prefix) as nested:
        nested.cookies.set(oidc.TRANSACTION_COOKIE, 'root-transaction', domain='portfolio.example', path='/')
        first = nested.get('api/auth/oidc/login')
        second = nested.get('api/auth/oidc/login')
        assert first.headers['set-cookie'] != second.headers['set-cookie']
        assert db.query(OIDCLoginTransaction).count() == 1
        state = parse_qs(urlsplit(second.headers['location']).query)['state'][0]
        result = nested.get('api/auth/oidc/callback', params={'state': state, 'error': 'access_denied'})
        assert result.headers['location'].endswith('oidc_error=cancelled')
        assert cookie_value(nested, config.transaction_cookie)
        assert 'set-cookie' not in result.headers
        assert nested.cookies[oidc.TRANSACTION_COOKIE] == 'root-transaction'
        assert db.query(OIDCLoginTransaction).count() == 0


def test_selfcheck_get_cannot_run_probes_or_send_notifications(client, provider, monkeypatch):
    from unittest.mock import AsyncMock
    probe = AsyncMock(return_value={'items': [], 'notify_send': True})
    monkeypatch.setattr(health, 'run_selfcheck', probe)
    monkeypatch.setattr(health, 'list_selfcheck_items', lambda: [{'key': 'ai:1'}])
    complete(client, provider)
    for query in ('', '?notify_send=true', '?keys=ai:1', '?keys=notify:1&notify_send=true'):
        result = client.get('/api/health/selfcheck' + query)
        assert result.status_code == 405
        assert result.headers['allow'] == 'POST'
    assert client.head('/api/health/selfcheck?notify_send=true').status_code == 405
    listed = client.get('/api/health/selfcheck?list=1&notify_send=true')
    assert data(listed)['items'] == [{'key': 'ai:1'}]
    probe.assert_not_awaited()
    status = data(client.get('/api/auth/status'))
    for headers in ({}, {'X-CSRF-Token': status['csrf_token'], 'Origin': 'https://evil.example'}):
        assert client.post('/api/health/selfcheck?notify_send=true', headers=headers).status_code == 403
    probe.assert_not_awaited()
    headers = {'X-CSRF-Token': status['csrf_token'], 'Origin': ORIGIN}
    assert client.post('/api/health/selfcheck?notify_send=true&keys=ai:1,notify:1', headers=headers).status_code == 200
    probe.assert_awaited_once_with(notify_send=True, keys=['ai:1', 'notify:1'])


def test_logout_after_another_tab_rotates_session_requires_refreshed_csrf(client, provider, db):
    complete(client, provider)
    stale = data(client.get('/api/auth/status'))['csrf_token']
    complete(client, provider)
    result = client.post('/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': stale})
    assert result.status_code == 403
    assert result.json()['error_code'] == 'csrf_invalid'
    assert db.query(OIDCSession).count() == 1
    current = data(client.get('/api/auth/status'))['csrf_token']
    assert current != stale
    assert client.post('/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': current}).status_code == 200
    assert db.query(OIDCSession).count() == 0


@pytest.mark.parametrize('cancel', [False, True])
def test_oidc_http_redaction_context_resets_on_failure_or_cancellation(cancel, caplog):
    from src.platform.observability.log_context import redact_http_logs
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError if cancel else ValueError):
            with redact_http_logs():
                logging.getLogger('httpcore.http11').debug('receive_response_headers.complete OIDC_SECRET_CANARY')
                if cancel:
                    raise asyncio.CancelledError()
                raise ValueError('failure')
        logging.getLogger('httpcore.http11').debug('outside-scope-public-marker')
    assert 'outside-scope-public-marker' in caplog.text
    assert 'OIDC_SECRET_CANARY' not in caplog.text


def test_oidc_http_redaction_does_not_hide_concurrent_unrelated_diagnostics(caplog):
    from src.platform.observability.log_context import redact_http_logs

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def protected_provider():
            with redact_http_logs():
                entered.set()
                await release.wait()
                logging.getLogger('httpcore.http11').debug('receive_response_headers.complete OIDC_SECRET_CANARY')

        async def unrelated_provider():
            await entered.wait()
            logging.getLogger('httpcore.http11').debug('concurrent-public-marker')
            release.set()

        await asyncio.gather(protected_provider(), unrelated_provider())

    with caplog.at_level(logging.DEBUG):
        asyncio.run(exercise())
    assert 'concurrent-public-marker' in caplog.text
    assert 'OIDC_SECRET_CANARY' not in caplog.text
