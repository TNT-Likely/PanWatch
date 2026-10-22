"""Defensive regressions for delayed successful callback delivery."""
from fastapi.testclient import TestClient
import pytest

from test_oidc_cookie_lifecycle import assembly_client, start, complete, status
from test_oidc_auth import env, db, provider, ORIGIN


@pytest.mark.parametrize('logout_newer', [False, True])
def test_held_success_cannot_replace_newer_browser_state(assembly_client, provider, logout_newer):
    client, app, prefix = assembly_client
    state_a = start(client, prefix, provider)
    held = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    held.cookies.update(client.cookies)
    response_a = held.get(prefix + '/api/auth/oidc/callback', params={'state': state_a, 'code': 'signed-fixture-code'})
    assert response_a.headers['location'] == ORIGIN + prefix + '/'
    held.close()
    complete(client, prefix, provider)
    current = status(client, prefix)
    if logout_newer:
        response = client.post(prefix + '/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': current['csrf_token']})
        assert response.status_code == 200
    client.cookies.extract_cookies(response_a)
    after = status(client, prefix)
    assert after['authenticated'] is not logout_newer
    if not logout_newer:
        assert after['csrf_token'] == current['csrf_token']

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from starlette.requests import Request

from test_oidc_auth import app, client, ISSUER
from src.modules.administration import oidc
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import OIDCBrowserGeneration, OIDCLoginTransaction, OIDCSession


def remember_provider(provider):
    return {key: provider[key] for key in ('nonce', 'challenge')}


@pytest.mark.parametrize('arrival_order', ['old-first', 'new-first'])
def test_concurrent_first_starts_choose_server_generation(client, app, provider, db, arrival_order):
    # Neither request had a cookie when sent. Their response headers can arrive
    # in either order, without overwriting one another's bootstrap evidence.
    first = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    second = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    a = first.get('/api/auth/oidc/login')
    b = second.get('/api/auth/oidc/login')
    for response in ([a, b] if arrival_order == 'old-first' else [b, a]):
        client.cookies.extract_cookies(response)
    from urllib.parse import parse_qs, urlsplit
    a_params, b_params = [parse_qs(urlsplit(response.headers['location']).query) for response in (a, b)]
    old = client.get('/api/auth/oidc/callback', params={'state': a_params['state'][0], 'code': 'old'})
    assert old.headers['location'].endswith('oidc_error=failed')
    assert 'set-cookie' not in old.headers
    provider.update(nonce=b_params['nonce'][0], challenge=b_params['code_challenge'][0])
    newer = client.get('/api/auth/oidc/callback', params={'state': b_params['state'][0], 'code': 'new'})
    assert newer.headers['location'] == ORIGIN + '/'
    assert client.get('/api/private').status_code == 200
    assert db.query(OIDCSession).count() == 1
    first.close()
    second.close()


@pytest.mark.parametrize('cancel_newer', [False, True])
def test_concurrent_bootstrap_response_cannot_restore_old_success(client, app, provider, db, cancel_newer):
    from urllib.parse import parse_qs, urlsplit
    a_response = client.get('/api/auth/oidc/login')
    a = parse_qs(urlsplit(a_response.headers['location']).query)
    other_start = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    b_response = other_start.get('/api/auth/oidc/login')
    b = parse_qs(urlsplit(b_response.headers['location']).query)
    other_start.close()
    held = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    held.cookies.update(client.cookies)
    provider.update(nonce=a['nonce'][0], challenge=a['code_challenge'][0])
    success_a = held.get('/api/auth/oidc/callback', params={'state': a['state'][0], 'code': 'old'})
    assert success_a.headers['location'] == ORIGIN + '/'
    held.close()
    client.cookies.extract_cookies(b_response)
    provider.update(nonce=b['nonce'][0], challenge=b['code_challenge'][0])
    result = client.get('/api/auth/oidc/callback', params={'state': b['state'][0], **({'error': 'access_denied'} if cancel_newer else {'code': 'new'})})
    assert result.headers['location'].endswith('oidc_error=cancelled' if cancel_newer else '/')
    before = status(client, '')
    client.cookies.extract_cookies(success_a)
    assert status(client, '') == before
    assert before['authenticated'] is not cancel_newer


@pytest.mark.parametrize('outcome', ['cancel', 'deny', 'bad-token'])
def test_cancelled_or_failed_retry_preserves_presented_session(client, provider, db, outcome):
    complete(client, '', provider)
    original = status(client, '')
    state = start(client, '', provider)
    params = {'state': state, 'code': 'new'}
    if outcome == 'cancel':
        params = {'state': state, 'error': 'access_denied'}
    elif outcome == 'deny':
        provider['claims']['sub'] = 'not-allowed'
    else:
        provider['claims']['nonce'] = 'incorrect'
    response = client.get('/api/auth/oidc/callback', params=params)
    assert 'oidc_error=' in response.headers['location']
    assert 'set-cookie' not in response.headers
    assert status(client, '') == original
    assert db.query(OIDCBrowserGeneration).filter_by(revoked=False).count() == 1
    provider['claims'].clear()
    complete(client, '', provider)
    assert status(client, '')['csrf_token'] != original['csrf_token']


def test_logout_cancels_committed_but_undelivered_retry(client, app, provider, db):
    complete(client, '', provider)
    old_csrf = status(client, '')['csrf_token']
    state = start(client, '', provider)
    held = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    held.cookies.update(client.cookies)
    response = held.get('/api/auth/oidc/callback', params={'state': state, 'code': 'new'})
    held.close()
    assert not status(client, '')['authenticated']
    # Prior session is a bounded logout proof, never a route/status credential.
    result = client.post('/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': old_csrf})
    assert result.status_code == 200 and 'set-cookie' not in result.headers
    client.cookies.extract_cookies(response)
    assert not status(client, '')['authenticated']
    assert client.get('/api/private').status_code == 401
    assert db.query(OIDCSession).count() == 0


def test_logout_snapshot_cannot_revoke_unseen_new_login(client, app, provider):
    complete(client, '', provider)
    old_csrf = status(client, '')['csrf_token']
    delayed = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    delayed.cookies.update(client.cookies)
    complete(client, '', provider)
    newer = status(client, '')
    result = delayed.post('/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': old_csrf})
    delayed.close()
    assert result.status_code == 200 and 'set-cookie' not in result.headers
    assert status(client, '') == newer


@pytest.mark.parametrize('change', ['expire-proof', 'remove-owner', 'change-config'])
def test_logout_tombstone_obeys_expiry_and_configuration(client, app, provider, db, monkeypatch, change):
    complete(client, '', provider)
    old_csrf = status(client, '')['csrf_token']
    old = db.query(OIDCBrowserGeneration).one()
    state = start(client, '', provider)
    held = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    held.cookies.update(client.cookies)
    held.get('/api/auth/oidc/callback', params={'state': state, 'code': 'new'})
    held.close()
    if change == 'expire-proof':
        old.session_expires_at = 1
        db.commit()
    elif change == 'remove-owner':
        monkeypatch.setenv('OIDC_ALLOWED_SUBJECTS', '["different"]')
    else:
        monkeypatch.setenv('OIDC_CLIENT_ID', 'new-client')
    oidc.get_config.cache_clear()
    client.post('/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': old_csrf})
    assert db.query(OIDCSession).count() == 1


def test_independent_browsers_do_not_revoke_each_other(client, app, provider, db):
    complete(client, '', provider)
    original = status(client, '')
    other = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    complete(other, '', provider)
    other_csrf = status(other, '')['csrf_token']
    assert db.query(OIDCSession).count() == 2
    other.post('/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': other_csrf})
    assert status(client, '') == original
    assert not status(other, '')['authenticated']
    other.close()


def test_binding_outlives_transaction_to_invalidate_held_session(client, app, provider, monkeypatch):
    state = start(client, '', provider)
    held = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    held.cookies.update(client.cookies)
    response = held.get('/api/auth/oidc/callback', params={'state': state, 'code': 'old'})
    held.close()
    now = time.time()
    monkeypatch.setattr(oidc.time, 'time', lambda: now + oidc.TRANSACTION_SECONDS + 1)
    provider['claims'].update(iat=int(now), exp=int(now) + 600)
    complete(client, '', provider)
    current = status(client, '')
    client.post('/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': current['csrf_token']})
    client.cookies.extract_cookies(response)
    assert not status(client, '')['authenticated']


def test_sequential_retries_and_logins_keep_cookie_count_bounded(client, provider):
    for _ in range(12):
        state = start(client, '', provider)
        client.get('/api/auth/oidc/callback', params={'state': state, 'error': 'access_denied'})
        assert len(client.cookies) <= 1
    for _ in range(12):
        complete(client, '', provider)
        assert len(client.cookies) == 2


def test_many_delayed_bootstraps_recover_without_losing_current_session(client, app, provider, db):
    complete(client, '', provider)
    original = status(client, '')
    # Model 33 no-cookie login requests whose response headers arrive together.
    for _ in range(oidc.COOKIE_QUERY_BATCH + 1):
        bootstrap = TestClient(app, base_url=ORIGIN, follow_redirects=False)
        response = bootstrap.get('/api/auth/oidc/login')
        bootstrap.close()
        client.cookies.extract_cookies(response)
    assert status(client, '') == original
    state = start(client, '', provider)
    assert status(client, '') == original
    assert len(client.cookies) < oidc.COOKIE_QUERY_BATCH + 3
    assert db.query(OIDCLoginTransaction).count() == 1
    client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'new'})
    assert status(client, '')['authenticated']
    # Cleanup responses remain under a bounded Set-Cookie header budget.
    for _ in range(3):
        response = complete(client, '', provider)
        assert len(response.headers.get_list('set-cookie')) <= oidc.MAX_COOKIE_DELETIONS + 1
    assert len(client.cookies) == 2


def test_missing_generation_fails_closed_and_ids_are_never_reused(client, provider, db):
    complete(client, '', provider)
    old = db.query(OIDCBrowserGeneration).one().id
    identity = db.query(OIDCSession).one()
    assert oidc.session_is_active((identity.session_hash, identity.config_binding))
    db.query(OIDCBrowserGeneration).delete()
    db.commit()
    assert not status(client, '')['authenticated']
    assert not oidc.session_is_active((identity.session_hash, identity.config_binding))
    complete(client, '', provider)
    assert db.query(OIDCBrowserGeneration).one().id > old


def test_upgrade_adds_table_without_altering_legacy_schema(tmp_path, env, provider):
    engine = create_engine('sqlite:///' + str(tmp_path / 'legacy.sqlite'))
    for table in (OIDCLoginTransaction.__table__, OIDCSession.__table__):
        table.create(engine)
    def columns(table):
        return [{**column, 'type': str(column['type'])} for column in inspect(engine).get_columns(table)]
    old_columns = {table: columns(table) for table in ('oidc_login_transactions', 'oidc_sessions')}
    Base.metadata.create_all(engine)
    assert 'oidc_browser_generations' in inspect(engine).get_table_names()
    for table, previous in old_columns.items():
        assert columns(table) == previous
    with sessionmaker(bind=engine)() as db:
        config = oidc.get_config()
        db.add(OIDCSession(session_hash=oidc.digest('legacy-session'), csrf_token='legacy-csrf', issuer=ISSUER, subject='owner', config_binding=config.binding, expires_at=int(time.time()) + 100))
        db.add(OIDCLoginTransaction(state_hash=oidc.digest('legacy-state'), browser_hash=oidc.digest('legacy-binding'), nonce='nonce', verifier='verifier', config_binding=config.binding, expires_at=int(time.time()) + 100))
        db.commit()
        request = Request({'type': 'http', 'method': 'GET', 'headers': [(b'cookie', b'panwatch_oidc_session=legacy-session; panwatch_oidc_login=legacy-binding')]})
        assert oidc.get_session(db, request, config) is None
        with pytest.raises(oidc.OIDCError):
            oidc.consume_transaction(db, config, 'legacy-state', request)
    engine.dispose()


@pytest.fixture
def disk_client(app, monkeypatch, tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'concurrency.sqlite'), poolclass=NullPool,
        connect_args={'check_same_thread': False, 'timeout': 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    def connection():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = connection
    monkeypatch.setattr(oidc, 'SessionLocal', factory)
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        yield client, factory
    engine.dispose()


@pytest.mark.parametrize('interruption', ['logout', 'new-login', 'duplicate-callback'])
def test_pending_callback_uses_atomic_generation_claim(disk_client, app, provider, monkeypatch, interruption):
    client, factory = disk_client
    complete(client, '', provider)
    csrf = status(client, '')['csrf_token']
    state = start(client, '', provider)
    held = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    held.cookies.update(client.cookies)
    validated, release = Event(), Event()
    validate = oidc.validate_id_token
    first = True
    def pause_after_validation(*args):
        nonlocal first
        result = validate(*args)
        if first:
            first = False
            validated.set()
            assert release.wait(10)
        return result
    monkeypatch.setattr(oidc, 'validate_id_token', pause_after_validation)
    with ThreadPoolExecutor(max_workers=1) as executor:
        callback = executor.submit(held.get, '/api/auth/oidc/callback', params={'state': state, 'code': 'old'})
        assert validated.wait(10)
        try:
            if interruption == 'logout':
                result = client.post('/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf})
                assert result.status_code == 200
            elif interruption == 'new-login':
                complete(client, '', provider)
                newer = status(client, '')
            else:
                duplicate = client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'duplicate'})
                assert duplicate.headers['location'].endswith('oidc_error=failed')
        finally:
            release.set()
        response = callback.result(10)
    held.close()
    client.cookies.extract_cookies(response)
    if interruption == 'logout':
        assert response.headers['location'].endswith('oidc_error=failed')
        assert not status(client, '')['authenticated']
    elif interruption == 'new-login':
        assert response.headers['location'].endswith('oidc_error=failed')
        assert status(client, '') == newer
    else:
        assert response.headers['location'] == ORIGIN + '/'
        assert status(client, '')['authenticated']
    with factory() as db:
        assert db.query(OIDCSession).count() == (0 if interruption == 'logout' else 1)


def test_discovery_latency_cannot_reorder_first_start_generations(disk_client, app, provider, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    client, factory = disk_client
    early = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    entered, release = Event(), Event()
    discover = oidc.discover
    first = True
    def slow_first(*args):
        nonlocal first
        metadata = discover(*args)
        if first:
            first = False
            entered.set()
            assert release.wait(10)
        return metadata
    monkeypatch.setattr(oidc, 'discover', slow_first)
    with ThreadPoolExecutor(max_workers=1) as executor:
        older = executor.submit(early.get, '/api/auth/oidc/login')
        assert entered.wait(10)
        try:
            newer = client.get('/api/auth/oidc/login')
        finally:
            release.set()
        old_response = older.result(10)
    early.close()
    client.cookies.extract_cookies(old_response)
    a, b = [parse_qs(urlsplit(response.headers['location']).query) for response in (old_response, newer)]
    rejected = client.get('/api/auth/oidc/callback', params={'state': a['state'][0], 'code': 'old'})
    assert rejected.headers['location'].endswith('oidc_error=failed')
    provider.update(nonce=b['nonce'][0], challenge=b['code_challenge'][0])
    assert client.get('/api/auth/oidc/callback', params={'state': b['state'][0], 'code': 'new'}).headers['location'] == ORIGIN + '/'
    assert status(client, '')['authenticated']


def fallback_token(db, monkeypatch):
    from src.modules.administration.api import auth
    monkeypatch.setenv('OIDC_ALLOW_PASSWORD_LOGIN', 'true')
    oidc.get_config.cache_clear()
    auth.set_password_hash(db, auth.hash_password('disposable-fixture'))
    return auth.create_token()[0]


@pytest.mark.parametrize('committed', [False, True])
def test_password_fallback_logout_cancels_observed_oidc_attempt(client, app, provider, db, monkeypatch, committed):
    token = fallback_token(db, monkeypatch)
    state = start(client, '', provider)
    saved = remember_provider(provider)
    response = None
    if committed:
        held = TestClient(app, base_url=ORIGIN, follow_redirects=False)
        held.cookies.update(client.cookies)
        response = held.get('/api/auth/oidc/callback', params={'state': state, 'code': 'held'})
        held.close()
    logout = client.post('/api/auth/logout', headers={'Origin': ORIGIN, 'Authorization': 'Bearer ' + token})
    assert logout.status_code == 200 and 'set-cookie' not in logout.headers
    if response is not None:
        client.cookies.extract_cookies(response)
    else:
        provider.update(saved)
        assert client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'pending'}).headers['location'].endswith('oidc_error=failed')
    assert not status(client, '')['authenticated']
    assert db.query(OIDCSession).count() == 0


@pytest.mark.parametrize('invalid', ['bad-token', 'expired-token', 'sso-only', 'no-password', 'no-origin', 'foreign-origin', 'no-cookie'])
def test_password_logout_cannot_cancel_without_required_proof(client, app, provider, db, monkeypatch, invalid):
    from src.modules.administration.api import auth
    from src.platform.persistence.models import AppSettings
    token = fallback_token(db, monkeypatch)
    state = start(client, '', provider)
    if invalid == 'bad-token':
        token = 'invalid'
    elif invalid == 'expired-token':
        token = auth.create_token(expires_days=-1)[0]
    elif invalid == 'sso-only':
        monkeypatch.setenv('OIDC_ALLOW_PASSWORD_LOGIN', 'false')
        oidc.get_config.cache_clear()
    elif invalid == 'no-password':
        db.query(AppSettings).filter_by(key=auth.PASSWORD_HASH_KEY).delete()
        db.commit()
    headers = {'Authorization': 'Bearer ' + token}
    if invalid != 'no-origin':
        headers['Origin'] = 'https://other.example' if invalid == 'foreign-origin' else ORIGIN
    if invalid == 'no-cookie':
        empty = TestClient(app, base_url=ORIGIN, follow_redirects=False)
        result = empty.post('/api/auth/logout', headers=headers)
        empty.close()
    else:
        result = client.post('/api/auth/logout', headers=headers)
    assert result.status_code == (403 if invalid in {'no-origin', 'foreign-origin'} else 200)
    assert 'set-cookie' not in result.headers
    assert db.query(OIDCLoginTransaction).filter_by(state_hash=oidc.digest(state)).count() == 1
    assert client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'still-current'}).headers['location'] == ORIGIN + '/'


def test_password_logout_snapshot_does_not_revoke_unseen_login(client, app, provider, db, monkeypatch):
    token = fallback_token(db, monkeypatch)
    start(client, '', provider)
    stale = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    stale.cookies.update(client.cookies)
    complete(client, '', provider)
    current = status(client, '')
    stale.post('/api/auth/logout', headers={'Origin': ORIGIN, 'Authorization': 'Bearer ' + token})
    stale.close()
    assert status(client, '') == current


def test_terminal_attempt_retention_does_not_shorten_issued_logout_proof(client, provider, db):
    complete(client, '', provider)
    issued = db.query(OIDCBrowserGeneration).one()
    original_expiry = issued.expires_at
    state = start(client, '', provider)
    client.get('/api/auth/oidc/callback', params={'state': state, 'error': 'access_denied'})
    db.expire_all()
    cancelled = db.query(OIDCBrowserGeneration).filter(OIDCBrowserGeneration.id != issued.id).one()
    assert cancelled.revoked and cancelled.expires_at <= int(time.time()) + oidc.TRANSACTION_SECONDS
    assert issued.expires_at == original_expiry and not issued.revoked
    csrf = status(client, '')['csrf_token']
    client.post('/api/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf})
    db.expire_all()
    assert issued.revoked and issued.expires_at == original_expiry


@pytest.mark.parametrize('failure', ['http', 'metadata'])
def test_failed_discovery_preserves_presented_session_and_retry_recovers(client, provider, db, failure):
    complete(client, '', provider)
    original = status(client, '')
    first_id = db.query(OIDCBrowserGeneration).one().id
    if failure == 'http':
        provider['status'] = 503
    else:
        provider['metadata']['issuer'] = 'https://invalid.example'
    response = client.get('/api/auth/oidc/login')
    assert response.headers['location'].endswith('oidc_error=unavailable')
    assert 'set-cookie' not in response.headers
    assert status(client, '') == original
    assert db.query(OIDCLoginTransaction).count() == 0
    failed = db.query(OIDCBrowserGeneration).filter(OIDCBrowserGeneration.id != first_id).one()
    assert failed.revoked and failed.session_hash is None
    assert failed.expires_at <= int(time.time()) + oidc.TRANSACTION_SECONDS
    provider['status'] = 200
    provider['metadata']['issuer'] = ISSUER
    complete(client, '', provider)
    assert status(client, '')['csrf_token'] != original['csrf_token']
    assert db.query(OIDCSession).count() == 1


@pytest.mark.parametrize('binding_state', ['missing', 'corrupt', 'expired'])
def test_held_success_requires_its_original_browser_binding(client, app, provider, binding_state):
    from test_oidc_auth import cookie_name
    state = start(client, '', provider)
    held = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    held.cookies.update(client.cookies)
    response = held.get('/api/auth/oidc/callback', params={'state': state, 'code': 'held'})
    held.close()
    name = cookie_name(client, oidc.get_config().transaction_cookie)
    if binding_state == 'expired':
        for cookie in client.cookies.jar:
            if cookie.name == name:
                cookie.expires = int(time.time()) - 1
    else:
        client.cookies.delete(name)
        if binding_state == 'corrupt':
            client.cookies.set(name, 'wrong-binding', domain='portfolio.example', path='/')
    client.cookies.extract_cookies(response)
    assert not status(client, '')['authenticated']
    assert client.get('/api/private').status_code == 401
    complete(client, '', provider)
    assert status(client, '')['authenticated']


def test_session_and_binding_must_belong_to_same_generation(client, app, provider):
    from test_oidc_auth import cookie_name, cookie_value
    complete(client, '', provider)
    other = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    complete(other, '', provider)
    mixed = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    config = oidc.get_config()
    mixed.cookies.set(cookie_name(client, config.transaction_cookie), cookie_value(client, config.transaction_cookie))
    mixed.cookies.set(cookie_name(other, config.session_cookie), cookie_value(other, config.session_cookie))
    assert not status(mixed, '')['authenticated']
    assert mixed.get('/api/private').status_code == 401
    assert status(client, '')['authenticated'] and status(other, '')['authenticated']
    mixed.close()
    other.close()
