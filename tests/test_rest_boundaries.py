"""Production API/routing and persistent logging boundaries, local receivers only."""
import asyncio
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from test_rest_notifications import certificate, gateway, policy
from src.platform.notifications import custom_rest as rest
from src.platform.notifications.notifier import NotifierManager
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import AgentConfig, AppSettings, LogEntry


@pytest.fixture(autouse=True)
def _suppress_notifications():
    # Exercise production sends, exclusively against disposable local receivers.
    pass


@contextmanager
def durable_gateway(path):
    """A shared gateway DB implements the documented per-delivery uniqueness."""
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE events (source TEXT, delivery_id TEXT, destination TEXT, UNIQUE(source, delivery_id))')
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            event = json.loads(body)
            calls.append((self.path, self.headers['Idempotency-Key'], body))
            with sqlite3.connect(path) as db:
                inserted = db.execute('INSERT OR IGNORE INTO events VALUES (?, ?, ?)',
                                      (event['source'], event['delivery_id'], self.path)).rowcount
            # The first response after a durable commit fails: clients must
            # replay the same delivery rather than enqueueing another one.
            self.send_response(503 if inserted else 202)
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_authenticated_selected_destinations_enqueue_and_replay_independently(tmp_path, monkeypatch):
    import server
    from src.modules.administration.api import auth

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    def get_qa_db():
        with factory() as db:
            yield db
    monkeypatch.setitem(server.app.dependency_overrides, get_db, get_qa_db)
    monkeypatch.setattr(server, 'SessionLocal', factory)
    monkeypatch.setattr(auth, 'SessionLocal', factory)
    with factory() as db:
        db.add_all([
            AgentConfig(name='daily_report', display_name='Daily report', kind='workflow', config={}),
            AppSettings(key='auth_username', value='qa-rest'),
            AppSettings(key='auth_password_hash', value=auth.hash_password('qa-only-password')),
            AppSettings(key='notify_retry_attempts', value='2'),
            AppSettings(key='notify_retry_backoff_seconds', value='0'),
        ])
        db.commit()
    # No lifespan: do not start scheduler or real market-data background jobs.
    client = TestClient(server.app)
    try:
        assert client.get('/api/channels').status_code == 401
        login = client.post('/api/auth/login', json={'username': 'qa-rest', 'password': 'qa-only-password'})
        assert login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['data']['token']
        with durable_gateway(tmp_path / 'gateway.db') as (origin, calls):
            ids = []
            for destination in ('chat-one', 'chat-two'):
                created = client.post('/api/channels', json={
                    'name': destination, 'type': 'custom_rest',
                    'config': {'endpoint_url': origin + '/' + destination},
                })
                assert created.status_code == 200, created.text
                ids.append(created.json()['data']['id'])
            assigned = client.put('/api/agents/daily_report', json={'notify_channel_ids': ids})
            assert assigned.status_code == 200
            assert assigned.json()['data']['notify_channel_ids'] == ids
            channels = server.resolve_notify_channels('daily_report')
            assert [channel.id for channel in channels] == ids
            metadata = {'message_id': 'report-stable-id', 'occurred_at': datetime(2026, 10, 10)}
            result = asyncio.run(server._build_notifier(channels).notify_with_result('Report', 'Both routes', **metadata))
            assert result == {'success': True}
            assert len(calls) == 4
            assert calls[0] == calls[1] and calls[2] == calls[3]
            events = [json.loads(call[2]) for call in calls]
            assert {event['event_id'] for event in events} == {'report-stable-id'}
            assert len({event['timestamp'] for event in events}) == 1
            assert len({event['delivery_id'] for event in events}) == 2
            assert len({call[1] for call in calls}) == 2
            assert all(call[1] == rest.publication_id(event) for call, event in zip(calls, events))
            # Fresh manager, reversed selection order and repaired timeout must
            # retain delivery IDs and payloads on an explicit logical replay.
            for channel in channels:
                channel.config = channel.config | {'timeout_seconds': '7'}
            result = asyncio.run(server._build_notifier(channels[::-1]).notify_with_result('Report', 'Both routes', **metadata))
            assert result == {'success': True}
            assert calls[4:] == [calls[2], calls[0]]
            with sqlite3.connect(tmp_path / 'gateway.db') as db:
                assert db.execute('SELECT destination FROM events ORDER BY destination').fetchall() == [('/chat-one',), ('/chat-two',)]
    finally:
        client.close()
        engine.dispose()


def test_destination_identity_uses_auth_reference_without_resolving_secrets(monkeypatch):
    envelope = rest.notification_envelope('Report', 'Body', message_id='stable')
    config = {'endpoint_url': 'https://gateway.example/events', 'token_env': 'QA_CHAT_ONE'}
    first = rest.destination_envelope(envelope, config)
    second = rest.destination_envelope(envelope, config | {'token_env': 'QA_CHAT_TWO'})
    assert first['event_id'] == second['event_id'] == envelope['event_id']
    assert first['delivery_id'] != second['delivery_id']
    monkeypatch.setenv('QA_CHAT_ONE', 'rotated-qa-token')
    assert rest.destination_envelope(envelope, config | {'tls_ca_env': 'QA_NEW_CA', 'timeout_seconds': '9'}) == first
    assert 'rotated-qa-token' not in json.dumps(first)


@pytest.fixture
def production_logs(tmp_path, monkeypatch):
    import server
    from src.platform.observability import log_handler
    engine = create_engine(f"sqlite:///{tmp_path / 'logs.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(log_handler, 'SessionLocal', factory)
    monkeypatch.setattr(log_handler, '_ACTIVE_HANDLER', None)
    monkeypatch.delenv('LOG_LEVEL', raising=False)
    root = logging.getLogger()
    original_handlers, original_level = root.handlers[:], root.level
    uvicorn_state = [(logging.getLogger(name), logging.getLogger(name).handlers[:],
                      logging.getLogger(name).propagate, logging.getLogger(name).level)
                     for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access')]
    server.setup_logging()
    assert root.level == logging.DEBUG
    db_handler = next(handler for handler in root.handlers if isinstance(handler, log_handler.DBLogHandler))
    assert db_handler.level == logging.DEBUG
    def persisted():
        # Close flushes and joins the actual DB writer; inspect persisted rows.
        root.removeHandler(db_handler)
        db_handler.close()
        with factory() as db:
            return [(row.logger_name, row.message) for row in db.query(LogEntry).all()]
    try:
        yield persisted
    finally:
        for handler in root.handlers[:]:
            if handler not in original_handlers:
                root.removeHandler(handler); handler.close()
        if db_handler in root.handlers:
            root.removeHandler(db_handler)
        db_handler.close()
        root.handlers = original_handlers
        root.setLevel(original_level)
        for logger, handlers, propagate, level in uvicorn_state:
            logger.handlers, logger.propagate, logger.level = handlers, propagate, level
        engine.dispose()


@pytest.mark.parametrize('status', [202, 401, 503])
@pytest.mark.parametrize('reflect_bearer', [False, True])
def test_production_debug_database_omits_rest_response_secrets(status, reflect_bearer, certificate, monkeypatch, production_logs):
    tls, cert = certificate
    monkeypatch.setenv('QA_REST_TOKEN', 'qa-bearer-secret')
    monkeypatch.setenv('QA_REST_CA', cert)
    response_headers = {'Set-Cookie': 'gateway_session=qa-cookie-secret; Secure; HttpOnly'}
    if reflect_bearer:
        response_headers['WWW-Authenticate'] = 'Bearer error_description="qa-bearer-secret"'
    with gateway((status,), tls=tls, response_headers=response_headers) as (config, calls):
        manager = NotifierManager(policy=policy())
        manager.add_channel('custom_rest', config | {'token_env': 'QA_REST_TOKEN', 'tls_ca_env': 'QA_REST_CA'})
        result = asyncio.run(manager.notify_with_result('QA', 'Body'))
        assert result['success'] == (status == 202)
        assert calls[0][0]['Authorization'] == 'Bearer qa-bearer-secret'
        assert len(calls) == (3 if status == 503 else 1)
    rows = production_logs()
    messages = '\n'.join(message for _, message in rows)
    assert 'qa-cookie-secret' not in messages and 'qa-bearer-secret' not in messages
    assert 'Set-Cookie' not in messages and 'WWW-Authenticate' not in messages
    assert 'receive_response_headers.complete (details redacted)' in messages
    assert f'REST gateway returned HTTP {status}' in messages


def test_rest_redaction_keeps_concurrent_unrelated_http_diagnostics(production_logs):
    async def exercise():
        received, release, disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def slow_gateway(reader, writer):
            try:
                headers = await reader.readuntil(b'\r\n\r\n')
                length = next(int(line.split(b':')[1]) for line in headers.split(b'\r\n') if line.lower().startswith(b'content-length:'))
                await reader.readexactly(length)
                received.set()
                await release.wait()
                writer.write(b'HTTP/1.1 202 qa-reason-secret\r\nSet-Cookie: qa-cookie-secret\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                disconnected.set()
        server = await asyncio.start_server(slow_gateway, '127.0.0.1', 0)
        try:
            manager = NotifierManager()
            manager.add_channel('custom_rest', {'endpoint_url': f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/events'})
            task = asyncio.create_task(manager.notify_with_result('QA', 'Body'))
            await asyncio.wait_for(received.wait(), 2)
            # Another real HTTP request while the REST task is still awaiting
            # headers: its diagnostics must not be globally disabled/redacted.
            with gateway(response_headers={'X-Other-Provider': 'public-concurrent-marker'}) as (config, _):
                async with httpx.AsyncClient(trust_env=False) as client:
                    assert (await client.post(config['endpoint_url'], content=b'QA')).status_code == 202
            release.set()
            assert (await task)['success']
            await asyncio.wait_for(disconnected.wait(), 2)
        finally:
            release.set()
            server.close()
            await server.wait_closed()
    asyncio.run(exercise())
    messages = '\n'.join(message for _, message in production_logs())
    assert 'public-concurrent-marker' in messages
    assert 'qa-cookie-secret' not in messages and 'qa-reason-secret' not in messages


@pytest.mark.parametrize('cancel', [False, True])
def test_http_redaction_context_resets_on_failure_or_cancellation(cancel, caplog):
    from src.platform.observability.log_context import redact_http_logs
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError if cancel else ValueError):
            with redact_http_logs():
                logging.getLogger('httpcore.http11').debug('receive_response_headers.complete secret')
                if cancel:
                    raise asyncio.CancelledError()
                raise ValueError('failure')
        logging.getLogger('httpcore.http11').debug('outside-scope-public-marker')
    assert 'outside-scope-public-marker' in caplog.text
    assert 'complete secret' not in caplog.text
