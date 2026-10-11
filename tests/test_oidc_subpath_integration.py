"""OIDC production assembly at root and, when installed, both subpath modes."""
import importlib
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from starlette.responses import StreamingResponse

from test_oidc_auth import env, db, provider, ORIGIN
from src.modules.administration import oidc
from src.modules.administration.api.auth import get_current_user
from src.platform.persistence.database import get_db


@pytest.mark.parametrize('prefix,mode', [
    ('', 'preserve'),
    ('/panwatch', 'preserve'), ('/panwatch', 'strip'),
    ('/apps/panwatch', 'preserve'), ('/apps/panwatch', 'strip'),
])
def test_production_oidc_callback_cookie_api_sse_and_logout(db, provider, monkeypatch, tmp_path, mode, prefix):
    subpaths_available = importlib.util.find_spec('src.web.spa') is not None
    if prefix and not subpaths_available:
        pytest.skip('Requires the independently reviewed reverse-proxy subpath feature')
    monkeypatch.setenv('PANWATCH_BASE_PATH', prefix)
    monkeypatch.setenv('PANWATCH_PROXY_MODE', mode)
    monkeypatch.setenv('OIDC_PUBLIC_URL', ORIGIN + prefix)
    oidc.get_config.cache_clear()
    monkeypatch.setattr(oidc, 'SessionLocal', sessionmaker(bind=db.bind))
    import src.bootstrap.application as assembly
    # Restore the shared module after this deployment-specific assembly probe.
    monkeypatch.setattr(assembly, "app", assembly.app)
    monkeypatch.setattr(assembly, "application_lifespan", assembly.application_lifespan)
    app = importlib.reload(assembly).app
    app.dependency_overrides[get_db] = lambda: db

    @app.post('/api/contract-stream')
    def contract_stream(user=Depends(get_current_user)):
        return StreamingResponse(iter([b'data: owner-event\n\n']), media_type='text/event-stream')

    static = tmp_path / 'static'
    static.mkdir()
    (static / 'index.html').write_text('<html><base href="/" data-panwatch-base><meta name="panwatch-base-path" content="">synthetic SPA</html>')
    if subpaths_available:
        from src.web.spa import install_spa
        install_spa(app, static, app.state.public_base_path)
    else:
        from src.web.static_assets import serve_static_asset

        @app.get('/{path:path}')
        async def serve_spa(path: str):
            return serve_static_asset(str(static), path)

    async def proxy(scope, receive, send):
        if mode == 'strip' and scope['type'] == 'http':
            scope = dict(scope)
            assert scope['path'].startswith(prefix + '/')
            scope['path'] = scope['path'][len(prefix):]
            scope['raw_path'] = scope['raw_path'][len(prefix):]
        await app(scope, receive, send)

    client = TestClient(proxy, base_url=ORIGIN, follow_redirects=False)
    status = client.get(prefix + '/api/auth/status')
    assert status.status_code == 200
    assert status.json()['data']['oidc_login_url'] == ORIGIN + prefix + '/api/auth/oidc/login'
    assert client.get(prefix + '/api/accounts').status_code == 401
    started = client.get(prefix + '/api/auth/oidc/login')
    assert started.status_code == 303
    params = parse_qs(urlsplit(started.headers['location']).query)
    assert params['redirect_uri'] == [ORIGIN + prefix + '/api/auth/oidc/callback']
    provider['nonce'] = params['nonce'][0]
    provider['challenge'] = params['code_challenge'][0]
    response = client.get(prefix + '/api/auth/oidc/callback', params={'state': params['state'][0], 'code': 'mock-code'})
    assert response.status_code == 303 and response.headers['location'] == ORIGIN + prefix + '/'
    assert f'Path={prefix}/' in response.headers['set-cookie']
    assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie']
    page = client.get(prefix + '/')
    assert page.status_code == 200 and f'content="{prefix}"' in page.text
    status = client.get(prefix + '/api/auth/status').json()['data']
    assert status['authenticated'] and status['auth_method'] == 'oidc'
    headers = {'Origin': ORIGIN, 'X-CSRF-Token': status['csrf_token']}
    accounts = client.get(prefix + '/api/accounts')
    assert accounts.status_code == 200 and accounts.headers['cache-control'] == 'no-store'
    assert client.post(prefix + '/api/accounts', json={'name': 'Synthetic account', 'available_funds': 0}, headers=headers).status_code == 200
    assert client.post(prefix + '/api/accounts', json={'name': 'Rejected account', 'available_funds': 0}).status_code == 403
    assert client.post(prefix + '/api/contract-stream', headers=headers).text == 'data: owner-event\n\n'
    assert client.post(prefix + '/api/contract-stream').status_code == 403
    assert client.post(prefix + '/api/auth/logout', headers=headers).status_code == 200
    assert not client.get(prefix + '/api/auth/status').json()['data']['authenticated']
    assert client.get(prefix + '/api/accounts').status_code == 401
    assert client.get(prefix + '/login').status_code == 200
    client.close()
