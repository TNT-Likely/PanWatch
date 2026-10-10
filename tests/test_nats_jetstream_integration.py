"""Opt-in, disposable loopback broker test; never uses user broker configuration.

Run: PANWATCH_TEST_NATS_SERVER=/path/to/nats-server python -m pytest \
    tests/test_nats_jetstream_integration.py -q

The binary must come from an official NATS release. No persistent host service,
credentials, external notifications or Telegram consumer are configured.
"""
import asyncio
import json
import os
import socket
import ssl
import subprocess
import time

import nats
import pytest
from nats.js.api import ConsumerConfig, AckPolicy, StreamConfig, StorageType

from src.platform.notifications.nats_jetstream import notification_envelope, publish_notification


@pytest.fixture
def broker(tmp_path, request, monkeypatch):
    binary = os.environ.get("PANWATCH_TEST_NATS_SERVER")
    if not binary:
        pytest.skip("Set PANWATCH_TEST_NATS_SERVER to an official nats-server binary")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (tmp_path / "broker.log").open("wb")
    args = [binary, "-js", "-a", "127.0.0.1", "-p", str(port), "-sd", str(tmp_path / "data")]
    tls = getattr(request, "param", False)
    if tls:
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"], check=True, capture_output=True)
        config_file = tmp_path / "nats.conf"
        config_file.write_text(f'tls {{cert_file: "{cert}", key_file: "{key}", handshake_first: true}}\nauthorization {{token: "integration-only-token"}}')
        args += ["-c", str(config_file)]
        monkeypatch.setenv("PANWATCH_TEST_BROKER_CA", cert.read_text())
        monkeypatch.setenv("PANWATCH_TEST_BROKER_TOKEN", "integration-only-token")
    process = subprocess.Popen(args, stdout=log, stderr=log)
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Disposable test broker exited")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Disposable test broker did not start")
        yield f"tls://localhost:{port}" if tls else f"nats://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


@pytest.mark.parametrize("broker", [False, True], indirect=True)
def test_real_puback_offline_durable_consumer_dedup_and_missing_stream(broker):
    async def scenario():
        config = {"server_url": broker, "subject": "panwatch.notifications.v1", "stream": "PANWATCH_TEST"}
        options = {}
        if broker.startswith("tls:"):
            config["tls_ca_env"] = "PANWATCH_TEST_BROKER_CA"
            config["token_env"] = "PANWATCH_TEST_BROKER_TOKEN"
            options = {"tls": ssl.create_default_context(cadata=os.environ["PANWATCH_TEST_BROKER_CA"]), "tls_handshake_first": True, "token": "integration-only-token"}
        admin = await nats.connect(broker, **options)
        try:
            js = admin.jetstream()
            await js.add_stream(config=StreamConfig(name=config["stream"], subjects=["panwatch.>"], storage=StorageType.FILE))
            await js.add_consumer(config["stream"], config=ConsumerConfig(durable_name="TEST_OFFLINE", ack_policy=AckPolicy.EXPLICIT))
        finally:
            await admin.close()
        # No consumer client is connected while PanWatch publishes.
        envelope = notification_envelope("盯盘 📈", "Café notification", message_id="stable-integration-event")
        await publish_notification(config, envelope)
        await publish_notification(config, envelope)  # ambiguous-ack retry equivalent
        await publish_notification(config | {"subject": "panwatch.other.v1"}, envelope)
        consumer = await nats.connect(broker, **options)
        try:
            js = consumer.jetstream()
            info = await js.stream_info(config["stream"])
            assert info.state.messages == 2  # one deduped retry, two distinct routes
            sub = await js.pull_subscribe_bind(stream=config["stream"], consumer="TEST_OFFLINE")
            messages = await sub.fetch(2, timeout=2)
            assert {msg.subject for msg in messages} == {config["subject"], "panwatch.other.v1"}
            for msg in messages:
                assert json.loads(msg.data) == envelope
                await msg.ack_sync()
            with pytest.raises(RuntimeError, match="no valid acknowledgement"):
                await publish_notification(config | {"stream": "MISSING"}, envelope)
            with pytest.raises(RuntimeError, match="no valid acknowledgement"):
                await publish_notification(config | {"subject": "outside.stream"}, envelope)
        finally:
            await consumer.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("scheme", ["tls", "TLS"])
def test_plaintext_info_cannot_downgrade_tls_or_capture_credentials(monkeypatch, scheme):
    """Adversarial local listener; real client must fail before any CONNECT/auth."""
    monkeypatch.setenv("PANWATCH_TEST_DOWNGRADE_TOKEN", "dummy-secret-never-on-wire")
    async def scenario():
        wire = []
        finished = asyncio.Event()
        async def plaintext_server(reader, writer):
            try:
                writer.write(b'INFO {"server_id":"test","version":"2.11.9","proto":1,"max_payload":1048576,"auth_required":true,"tls_required":false}\r\n')
                await writer.drain()
                wire.append(await asyncio.wait_for(reader.read(8192), timeout=2))
            finally:
                writer.close()
                await writer.wait_closed()
                finished.set()
        server = await asyncio.start_server(plaintext_server, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            config = {"server_url": f"{scheme}://127.0.0.1:{port}", "subject": "panwatch.test", "stream": "TEST", "token_env": "PANWATCH_TEST_DOWNGRADE_TOKEN", "timeout_seconds": "1"}
            with pytest.raises(RuntimeError, match="no valid acknowledgement"):
                await publish_notification(config, notification_envelope("Test", "Content"))
            await asyncio.wait_for(finished.wait(), timeout=3)
            assert wire
            assert b"CONNECT" not in b"".join(wire)
            assert b"dummy-secret-never-on-wire" not in b"".join(wire)
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(scenario())
