"""Exercise stock Apprise through its public API, never an external receiver.

These are capability/limitation probes, not tests of PanWatch's HTTP adapter.
Keep the real add(dict), payload construction, dispatch and Requests preparation
intact. Only the HTTP boundary is replaced, except for the loopback trickle test.
The observed gaps are pinned to the two reviewed releases, not a claim about
future Apprise APIs and not an upper bound on the application's dependency.
"""

import asyncio
import base64
import inspect
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import unquote_plus

import apprise
import pytest
import requests


pytestmark = pytest.mark.skipif(
    apprise.__version__ not in ("1.6.0", "1.13.1"),
    reason="stock Custom JSON characterization reviewed on Apprise 1.6.0 and 1.13.1",
)

HOST = "apprise-contract.invalid"
TITLE = "行情更新 📈"
BODY = "## 中文行情\n\n**涨幅** [详情](https://example.invalid/report) 🚀"


def _app(**options):
    app = apprise.Apprise(asset=apprise.AppriseAsset(secure_logging=True))
    assert app.add({
        "schema": "jsons", "host": HOST, "fullpath": "/events",
        "format": "markdown", **options,
    })
    return app


def _notify(app, asynchronous=False):
    kwargs = dict(title=TITLE, body=BODY, body_format=apprise.NotifyFormat.MARKDOWN)
    return asyncio.run(app.async_notify(**kwargs)) if asynchronous else app.notify(**kwargs)


def _json_details(app):
    return next(item["details"] for item in app.details()["schemas"]
                if item["service_name"] == "JSON")


def _response(request, status=202, body=b"", headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.headers.update(headers or {})
    response.request = request
    response.url = request.url
    return response


@pytest.fixture(autouse=True)
def http_boundary(monkeypatch, tmp_path):
    # Never consult real credentials, proxies or trust stores during the probes.
    for name in os.environ:
        if name.lower().endswith("_proxy") or name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("NETRC", str(tmp_path / "absent-netrc"))
    boundary = SimpleNamespace(calls=[], status=202, body=b"", headers={})

    def send(session, request, **kwargs):
        boundary.calls.append((request, kwargs, session.trust_env))
        return _response(request, boundary.status, boundary.body, boundary.headers)

    monkeypatch.setattr(requests.sessions.Session, "send", send)
    return boundary


@pytest.mark.parametrize("asynchronous", [False, True], ids=["notify", "async_notify"])
@pytest.mark.parametrize("with_extras", [False, True], ids=["default", "typed-extras"])
def test_public_json_envelope_preserves_unicode_markdown_and_typed_extras(http_boundary, asynchronous, with_extras):
    extras = {"event_id": "event-17", "delivery_id": "delivery-23", "channel_id": 23,
              "context": {"retry": False, "targets": [1, None, "中文"]}} if with_extras else {}
    assert _notify(_app(payload=extras), asynchronous)
    assert len(http_boundary.calls) == 1
    request, _, _ = http_boundary.calls[0]
    assert request.method == "POST"
    assert request.url == f"https://{HOST}/events"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.body) == {
        "version": "1.0", "title": TITLE, "message": BODY,
        "attachments": [], "type": "info", **extras,
    }


def test_public_json_payload_can_remap_and_remove_default_fields(http_boundary):
    assert _notify(_app(payload={"message": "body", "title": "subject",
                                 "attachments": None, "version": "", "type": False,
                                 "event_id": "event-17"}))
    assert json.loads(http_boundary.calls[0][0].body) == {
        "subject": TITLE, "body": BODY, "event_id": "event-17",
    }


@pytest.mark.parametrize("basic", [False, True], ids=["bearer", "basic"])
def test_public_json_supports_auth_and_idempotency_headers(http_boundary, basic):
    headers = {"Idempotency-Key": "delivery-23"}
    options = {"user": "qa-user", "password": "qa-password"} if basic else {}
    if not basic:
        headers["Authorization"] = "Bearer qa-only-token"
    assert _notify(_app(headers=headers, **options))
    request = http_boundary.calls[0][0]
    expected = "Basic " + base64.b64encode(b"qa-user:qa-password").decode() if basic else "Bearer qa-only-token"
    assert request.headers["Authorization"] == expected
    assert request.headers["Idempotency-Key"] == "delivery-23"
    assert request.method == "POST"


def test_redirect_no_is_ignored_by_160_but_honored_when_publicly_supported(http_boundary):
    app = _app(redirect="no")
    supported = "redirect" in _json_details(app)["args"]
    if apprise.__version__ == "1.6.0":
        assert not supported
    elif apprise.__version__ == "1.13.1":
        assert supported
    assert _notify(app)
    assert http_boundary.calls[0][1]["allow_redirects"] is (not supported)


def test_requests_inherits_ambient_proxy_even_with_trust_env_false_in_add_dict(http_boundary, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://qa-proxy.invalid:8123")
    assert _notify(_app(trust_env=False))
    _, options, trust_env = http_boundary.calls[0]
    assert trust_env is True
    assert options["proxies"]["https"] == "http://qa-proxy.invalid:8123"


def test_ambient_netrc_can_replace_explicit_bearer_header(http_boundary, monkeypatch, tmp_path):
    netrc = tmp_path / "qa.netrc"
    netrc.write_text(f"machine {HOST} login qa-netrc password qa-netrc-password\n")
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))
    assert _notify(_app(headers={"Authorization": "Bearer qa-explicit-token"}, trust_env=False))
    request, _, trust_env = http_boundary.calls[0]
    assert trust_env is True
    expected = "Basic " + base64.b64encode(b"qa-netrc:qa-netrc-password").decode()
    assert request.headers["Authorization"] == expected


def test_public_verify_is_boolean_not_per_destination_ca_path(http_boundary, monkeypatch, tmp_path):
    ca_path = str(tmp_path / "qa-ca.pem")
    app = _app(verify=ca_path)
    assert _json_details(app)["args"]["verify"]["type"] == "bool"
    assert _notify(app)
    # Supplying a Requests-style CA path does not configure that CA and can
    # actually disable verification. No TLS is performed by this probe.
    assert http_boundary.calls[-1][1]["verify"] is False
    assert _notify(_app())
    assert http_boundary.calls[-1][1]["verify"] is True
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", ca_path)
    assert _notify(_app())
    # The available Requests escape hatch is process-global ambient state.
    assert http_boundary.calls[-1][1]["verify"] == ca_path


@pytest.mark.parametrize("status", [401, 429, 503])
@pytest.mark.parametrize("asynchronous", [False, True], ids=["notify", "async_notify"])
def test_public_failure_result_does_not_expose_retry_after(http_boundary, status, asynchronous):
    http_boundary.status = status
    http_boundary.headers = {"Retry-After": "92317"}
    result = _notify(_app(), asynchronous)
    assert not result
    assert len(http_boundary.calls) == 1
    assert result is False


def test_debug_registration_and_failure_logs_expose_header_and_response(http_boundary, caplog):
    caplog.set_level(logging.DEBUG, logger="apprise")
    http_boundary.status = 503
    http_boundary.body = b"gateway reflected qa-response-secret"
    app = _app(headers={"Authorization": "Bearer qa-header-secret"})
    registration = unquote_plus(caplog.text)
    assert "qa-header-secret" in registration
    caplog.clear()
    assert not _notify(app)
    assert "qa-response-secret" in caplog.text


def test_async_cancellation_does_not_stop_inflight_http_send(monkeypatch):
    entered, release, finished = (threading.Event() for _ in range(3))
    outcomes = []

    def blocking_send(session, request, **kwargs):
        entered.set()
        try:
            assert release.wait(5), "test failed to release controlled HTTP send"
            outcomes.append("transport completed after cancellation")
            return _response(request)
        finally:
            finished.set()

    monkeypatch.setattr(requests.sessions.Session, "send", blocking_send)

    async def cancel():
        task = asyncio.create_task(_app(cto=0.1, rto=0.1).async_notify(body=BODY))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not finished.is_set()
            assert outcomes == []
        finally:
            release.set()
            await asyncio.to_thread(finished.wait, 3)
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(cancel())
    assert finished.is_set()
    assert outcomes == ["transport completed after cancellation"]


def test_socket_timeouts_do_not_bound_whole_attempt(monkeypatch):
    # Unlike a mocked sleep, this uses actual Requests socket read timeouts:
    # each byte arrives in time, but the response exceeds cto + rto together.
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(202)
            self.send_header("Content-Length", "9")
            self.end_headers()
            for _ in range(9):
                self.wfile.write(b".")
                self.wfile.flush()
                time.sleep(0.1)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}/events"
    calls = []

    def loopback_only(session, request, **kwargs):
        assert request.url == origin, "only the disposable local receiver is allowed"
        assert not kwargs["proxies"]
        calls.append(kwargs)
        # Bypass only the fixture replacement, keeping actual Requests I/O.
        return _REAL_SESSION_SEND(session, request, **kwargs)

    monkeypatch.setattr(requests.sessions.Session, "send", loopback_only)
    try:
        app = _app(schema="json", host="127.0.0.1", port=server.server_port,
                   cto=0.25, rto=0.25, redirect="no")
        assert "timeout" not in inspect.signature(app.notify).parameters
        started = time.monotonic()
        assert _notify(app)
        elapsed = time.monotonic() - started
        assert calls[0]["timeout"] == (0.25, 0.25)
        assert elapsed > sum(calls[0]["timeout"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


# Preserve Requests' normal boundary for the single explicitly local test.
_REAL_SESSION_SEND = requests.sessions.Session.send
