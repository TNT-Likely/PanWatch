"""Real notifier, disposable HTTP(S) gateway; no paid services or user receivers."""
import asyncio
import json
import ssl
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from src.platform.notifications import custom_rest as rest
from src.platform.notifications import webhook_transport
from src.platform.notifications.notifier import CHANNEL_TYPES, NotifierManager
from src.modules.administration.api.channels import validate_channel_config

CONFIG = {"endpoint_url": "https://gateway.example/events"}


@pytest.fixture(autouse=True)
def _suppress_notifications():
    # Deliberately override the global no-op fixture. All destinations are
    # disposable loopback servers or a mocked HTTP transport, never real users.
    pass


@contextmanager
def gateway(statuses=(202,), *, tls=None, response_headers=None):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            calls.append((dict(self.headers), body))
            self.send_response(statuses[min(len(calls) - 1, len(statuses) - 1)])
            for key, value in (response_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    if tls:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"endpoint_url": f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}/events"}, calls
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def send(config, *, policy=None, **kwargs):
    manager = NotifierManager(policy=policy)
    manager.add_channel('custom_rest', config)
    return asyncio.run(manager.notify_with_result('盯盘 📈', '**Price** ¥12\nCafé\x00', **kwargs))


def policy(attempts=2, quiet=False):
    return SimpleNamespace(retry_attempts=attempts, retry_backoff_seconds=0, is_quiet_now=lambda: quiet)


def test_real_gateway_receives_apprise_compatible_unicode_payload():
    with gateway() as (config, calls):
        assert send(config) == {"success": True}
    headers, body = calls[0]
    event = json.loads(body)
    assert set(event) == {'version', 'type', 'source', 'event_id', 'delivery_id', 'timestamp', 'title', 'message'}
    assert event['source'] == 'PanWatch' and event['version'] == '1.0'
    assert event['message'] == '**Price** ¥12\nCafé' and event['type'] == 'info'
    assert event['title'] == '盯盘 📈' and event['timestamp'].endswith('Z')
    assert headers['Idempotency-Key'] == rest.publication_id(event)
    assert headers['Content-Type'] == 'application/json'
    assert 'Authorization' not in headers


@pytest.mark.parametrize('status', [200, 201, 202, 204, 299])
def test_all_2xx_mean_gateway_acceptance(status):
    with gateway((status,)) as (config, calls):
        assert send(config)['success']
    assert len(calls) == 1


@pytest.mark.parametrize('status', [400, 401, 403, 404, 409, 413, 422, 302, 307, 308])
def test_permanent_errors_and_redirects_never_retry(status):
    with gateway((status,), response_headers={'Location': 'http://127.0.0.1:1/secret'}) as (config, calls):
        result = send(config, policy=policy(999))
    assert not result['success'] and result['retryable'] is False
    assert len(calls) == 1


@pytest.mark.parametrize('status', [429, 500, 502, 503])
def test_transient_retry_preserves_exact_payload_and_deduplication_key(status):
    with gateway((status, 202)) as (config, calls):
        assert send(config, policy=policy())['success']
    assert calls[0] == calls[1]


def test_retry_budget_is_bounded_and_outbox_does_not_multiply_it():
    with gateway((503,)) as (config, calls):
        assert not send(config, policy=policy(999))['success']
        assert len(calls) == 3
        assert not send(config, policy=policy(999), managed_delivery=True)['success']
        assert len(calls) == 4


def test_retry_after_is_observed_with_a_bounded_delay(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr('src.platform.notifications.notifier.asyncio.sleep', sleep)
    with gateway((429, 202), response_headers={'Retry-After': '99999'}) as (config, _):
        assert send(config, policy=policy())['success']
    sleep.assert_awaited_once_with(rest.MAX_RETRY_AFTER)


@pytest.mark.parametrize('value,expected', [('999999', 30), ('-2', 0), ('NaN', 0), ('bad', 0), ('1.5', 1.5), (None, 0), ('Thu, 01 Jan 1970 00:00:00 GMT', 0)])
def test_retry_after_parser(value, expected):
    assert rest.retry_after_seconds(value) == expected


def test_quiet_hours_bypass_and_stable_replay_metadata():
    with gateway() as (config, calls):
        assert send(config, policy=policy(quiet=True)) == {'success': False, 'skipped': 'quiet_hours'}
        assert not calls
        kwargs = {'message_id': 'price-alert:1:2:stable', 'occurred_at': datetime(2026, 1, 2, 3, 4, 5)}
        assert send(config, policy=policy(quiet=True), bypass_quiet_hours=True, **kwargs)['success']
        assert send(config, **kwargs)['success']
        assert calls[0] == calls[1]
        assert json.loads(calls[0][1])['timestamp'] == '2026-01-02T03:04:05Z'
        send(config)
        assert calls[2][0]['Idempotency-Key'] != calls[1][0]['Idempotency-Key']


@pytest.mark.parametrize('override', [
    {'endpoint_url': 'https://secret@host/events'}, {'endpoint_url': 'http://host/events'},
    {'endpoint_url': 'ftp://host/events'}, {'endpoint_url': 'https://host/events?token=secret'},
    {'endpoint_url': 'https://host/events#secret'}, {'endpoint_url': 'https://host:0'},
    {'endpoint_url': 'https://host:999999'}, {'endpoint_url': 'https://host/\r\ninject'},
    {'endpoint_url': 'http://127.0.0.2'}, {'endpoint_url': 'http://localhost.evil'},
    {'token': 'secret'}, {'headers': '{}'}, {'method': 'GET'}, {'verify': 'false'},
    {'token_env': 'not a name'}, {'timeout_seconds': 'nan'}, {'timeout_seconds': '0'},
    {'timeout_seconds': '11'}, {'timeout_seconds': 5},
    {'endpoint_url': 'http://127.0.0.1/events', 'token_env': 'SECRET'},
])
def test_invalid_config_is_rejected_by_runtime_and_api(override):
    config = CONFIG | override
    with pytest.raises(ValueError):
        send(config)
    with pytest.raises(HTTPException) as exc:
        validate_channel_config('custom_rest', config)
    assert exc.value.status_code == 400
    assert 'secret@' not in str(exc.value)


def test_payload_bound_identifiers_missing_env_and_bad_ca(monkeypatch):
    monkeypatch.delenv('PANWATCH_TEST_MISSING', raising=False)
    result = send(CONFIG | {'token_env': 'PANWATCH_TEST_MISSING'}, policy=policy(999))
    assert 'missing or empty environment variable' in result['error'] and not result['retryable']
    monkeypatch.setenv('PANWATCH_TEST_CA', 'bad-secret-ca')
    result = send(CONFIG | {'tls_ca_env': 'PANWATCH_TEST_CA'})
    assert 'valid PEM CA' in result['error'] and 'bad-secret-ca' not in str(result)
    monkeypatch.setenv('PANWATCH_TEST_TOKEN', 'secret\r\ninject')
    result = send(CONFIG | {'token_env': 'PANWATCH_TEST_TOKEN'})
    assert not result['success'] and 'inject' not in str(result)
    with pytest.raises(ValueError, match='256 KiB'):
        asyncio.run(rest.publish_notification(CONFIG, rest.notification_envelope('Title', '汉' * rest.MAX_PAYLOAD_BYTES)))
    assert not send(CONFIG, message_id='bad\r\nheader')['success']
    assert set(CHANNEL_TYPES['custom_rest']['fields']) == set(rest.CONFIG_FIELDS)


@pytest.fixture
def certificate(tmp_path):
    key, cert = tmp_path / 'key.pem', tmp_path / 'cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
                    '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context, cert.read_text()


def test_real_https_requires_trust_and_sends_auth_only_after_verification(certificate, monkeypatch, caplog):
    context, cert = certificate
    monkeypatch.setenv('PANWATCH_TEST_TOKEN', 'never-log-this-token')
    monkeypatch.setenv('PANWATCH_TEST_CA', cert)
    with gateway(tls=context) as (config, calls):
        config['token_env'] = 'PANWATCH_TEST_TOKEN'
        result = send(config)
        assert not result['success'] and not calls
        assert send(config | {'tls_ca_env': 'PANWATCH_TEST_CA'})['success']
        assert calls[0][0]['Authorization'] == 'Bearer never-log-this-token'
        assert 'never-log-this-token' not in calls[0][1].decode() + caplog.text + str(result)


def test_timeout_network_error_and_cancellation_close_client(monkeypatch, caplog):
    clients = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
            assert kwargs['verify'].verify_mode == ssl.CERT_REQUIRED and kwargs['verify'].check_hostname
            self.closed = False
            clients.append(self)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): self.closed = True
        def stream(self, *args, **kwargs): raise error
    monkeypatch.setattr(webhook_transport.httpx, 'AsyncClient', Client)
    for error in [TimeoutError('secret-token'), httpx.ConnectError('secret-token')]:
        result = send(CONFIG, policy=policy())
        assert not result['success'] and result['retryable']
        assert 'secret-token' not in str(result) + caplog.text
    error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError): send(CONFIG)
    assert all(client.closed for client in clients)


def test_channel_api_stores_and_exports_only_references(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from src.platform.persistence.database import Base
    from src.modules.administration.api.channels import ChannelCreate, ChannelUpdate, ChannelResponse, create_channel, update_channel, list_channels
    monkeypatch.setenv('PANWATCH_TEST_TOKEN', 'never-serialize-this-secret')
    engine = create_engine(f"sqlite:///{tmp_path / 'channels.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            row = create_channel(ChannelCreate(name='Gateway', type='custom_rest', config=CONFIG | {'token_env': 'PANWATCH_TEST_TOKEN'}), db)
            serialized = ChannelResponse.model_validate(list_channels(db)[0]).model_dump_json()
            assert 'PANWATCH_TEST_TOKEN' in serialized and 'never-serialize-this-secret' not in serialized
            with pytest.raises(HTTPException): update_channel(row.id, ChannelUpdate(config=CONFIG | {'token': 'forbidden-secret'}), db)
            db.refresh(row)
            assert 'token' not in row.config
            update_channel(row.id, ChannelUpdate(config=CONFIG | {'endpoint_url': 'https://other.example/events'}), db)
            assert row.config['endpoint_url'] == 'https://other.example/events'
    finally:
        engine.dispose()


def test_selfcheck_rejects_invalid_configuration():
    from src.modules.administration.selfcheck import probe_notify_channel
    channel = SimpleNamespace(id=1, name='Gateway', type='custom_rest', config=CONFIG | {'token': 'never-log-this'})
    result = asyncio.run(probe_notify_channel(channel, send=False))
    assert result['status'] == 'fail' and 'never-log-this' not in str(result)


@pytest.mark.parametrize('cancel', [False, True])
def test_real_socket_deadline_and_cancellation_release_connection(cancel):
    async def exercise():
        received, disconnected = asyncio.Event(), asyncio.Event()
        async def hold_response(reader, writer):
            try:
                headers = await reader.readuntil(b'\r\n\r\n')
                length = next(int(line.split(b':')[1]) for line in headers.split(b'\r\n') if line.lower().startswith(b'content-length:'))
                await reader.readexactly(length)
                received.set()
                assert await reader.read() == b''
                disconnected.set()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(hold_response, '127.0.0.1', 0)
        try:
            manager = NotifierManager()
            manager.add_channel('custom_rest', {'endpoint_url': f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/events', 'timeout_seconds': '1'})
            task = asyncio.create_task(manager.notify_with_result('Title', 'Content'))
            await asyncio.wait_for(received.wait(), 2)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
            else:
                result = await asyncio.wait_for(task, 2)
                assert not result['success'] and result['retryable']
            await asyncio.wait_for(disconnected.wait(), 2)
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(exercise())


def test_response_body_is_not_read_or_allowed_to_delay_acceptance():
    async def exercise():
        disconnected = asyncio.Event()
        async def never_finish_body(reader, writer):
            try:
                headers = await reader.readuntil(b'\r\n\r\n')
                length = next(int(line.split(b':')[1]) for line in headers.split(b'\r\n') if line.lower().startswith(b'content-length:'))
                await reader.readexactly(length)
                writer.write(b'HTTP/1.1 202 Accepted\r\nContent-Length: 1000000000\r\n\r\n')
                await writer.drain()
                assert await reader.read() == b''
                disconnected.set()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(never_finish_body, '127.0.0.1', 0)
        try:
            manager = NotifierManager()
            manager.add_channel('custom_rest', {'endpoint_url': f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/events'})
            assert (await asyncio.wait_for(manager.notify_with_result('Title', 'Content'), 1))['success']
            await asyncio.wait_for(disconnected.wait(), 2)
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(exercise())


def test_real_https_redirect_never_forwards_token(certificate, monkeypatch):
    context, cert = certificate
    monkeypatch.setenv('PANWATCH_TEST_TOKEN', 'never-forward-this-token')
    monkeypatch.setenv('PANWATCH_TEST_CA', cert)
    with gateway() as (target, target_calls):
        with gateway((307,), tls=context, response_headers={'Location': target['endpoint_url']}) as (config, source_calls):
            result = send(config | {'token_env': 'PANWATCH_TEST_TOKEN', 'tls_ca_env': 'PANWATCH_TEST_CA'}, policy=policy())
            assert not result['success'] and result['retryable'] is False
            assert len(source_calls) == 1 and not target_calls


@pytest.mark.parametrize('endpoint', ['https://192.168.1.10:8443/v1/events', 'https://notifications.internal/events', 'https://[::1]:8443/events'])
def test_verified_https_private_gateway_configuration_is_allowed(endpoint):
    assert rest.validate_config({'endpoint_url': endpoint, 'tls_ca_env': 'PRIVATE_CA_PEM'})['endpoint_url'] == endpoint


def test_payload_preserves_whitespace_and_long_markdown_without_splitting():
    # Shape compatibility does not import Apprise's default 32K overflow policy.
    title, message = '  Title\t ', '\n  **Long** ' + '文' * 33000 + '\t\n'
    with gateway() as (config, calls):
        manager = NotifierManager()
        manager.add_channel('custom_rest', config)
        assert asyncio.run(manager.notify_with_result(title, message))['success']
    assert len(calls) == 1
    event = json.loads(calls[0][1])
    assert event['title'] == title and event['message'] == message
    assert 'schema_version' not in event and 'content' not in event and 'format' not in event


def test_payload_byte_limit_is_checked_before_network(monkeypatch):
    envelope = rest.notification_envelope('Title', '')
    def encoded_size(event):
        return len(json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    envelope['message'] = 'a' * (rest.MAX_PAYLOAD_BYTES - encoded_size(envelope))
    transport = AsyncMock()
    monkeypatch.setattr(rest, 'post_notification', transport)
    asyncio.run(rest.publish_notification(CONFIG, envelope))
    assert len(transport.await_args.args[1]) == rest.MAX_PAYLOAD_BYTES
    transport.reset_mock()
    with pytest.raises(ValueError, match='256 KiB'):
        asyncio.run(rest.publish_notification(CONFIG, envelope | {'message': envelope['message'] + 'é'}))
    transport.assert_not_called()


def test_transport_ignores_ambient_proxy_and_netrc(tmp_path, monkeypatch):
    netrc = tmp_path / '.netrc'
    netrc.write_text('machine 127.0.0.1 login ambient-user password ambient-secret\n')
    netrc.chmod(0o600)
    monkeypatch.setenv('NETRC', str(netrc))
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    with gateway() as (config, calls):
        assert send(config)['success']
    assert len(calls) == 1 and 'Authorization' not in calls[0][0]
