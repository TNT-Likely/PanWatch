"""Exercise the real notification pipeline; only the NATS socket is mocked."""
import asyncio
import json
import ssl
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.platform.notifications import nats_jetstream as nats
from src.platform.notifications.notifier import CHANNEL_TYPES, NotifierManager
from src.modules.administration.api.channels import validate_channel_config

CONFIG = {"server_url": "tls://nats.example.net:4222", "subject": "panwatch.notifications.v1", "stream": "PANWATCH_NOTIFICATIONS"}


@pytest.fixture(autouse=True)
def _suppress_notifications():
    # Override the global fixture explicitly, including when --notify is absent.
    # This module replaces Client before any notification can use the network.
    pass


@pytest.fixture(autouse=True)
def client(monkeypatch):
    js = SimpleNamespace(publish=AsyncMock(return_value=SimpleNamespace(stream=CONFIG["stream"], seq=1)))
    nc = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(), jetstream=Mock(return_value=js))
    monkeypatch.setattr(nats, "Client", Mock(return_value=nc))
    return nc


def send(config=None, *, policy=None, **kwargs):
    manager = NotifierManager(policy=policy)
    manager.add_channel("nats_jetstream", CONFIG if config is None else config)
    return asyncio.run(manager.notify_with_result("盯盘 📈", "**Price** ¥12\nCafé\x00", **kwargs))


def test_acknowledged_unicode_json(client):
    assert send() == {"success": True}
    call = client.jetstream().publish.call_args
    assert call.args[0] == CONFIG["subject"]
    envelope = json.loads(call.args[1])
    assert set(envelope) == {"schema_version", "message_id", "timestamp", "source", "title", "content"}
    assert envelope["schema_version"] == 1
    assert envelope["source"] == "PanWatch"
    assert envelope["title"] == "盯盘 📈"
    assert envelope["content"] == "**Price** ¥12\nCafé"
    assert envelope["timestamp"].endswith("Z")
    assert call.kwargs["headers"]["Nats-Msg-Id"] == nats.publication_id(CONFIG, envelope["message_id"])
    assert call.kwargs["stream"] == CONFIG["stream"]
    client.close.assert_awaited_once()


@pytest.mark.parametrize("ack", [None, SimpleNamespace(stream="WRONG", seq=1), SimpleNamespace(stream=CONFIG["stream"], seq=0)])
def test_invalid_ack_fails(client, ack):
    client.jetstream().publish.return_value = ack
    assert send()["success"] is False
    client.close.assert_awaited_once()


@pytest.mark.parametrize("error", [TimeoutError("secret-token"), RuntimeError("no stream matches; secret-token"), ConnectionError("secret-token")])
def test_errors_and_logs_are_redacted(client, caplog, error):
    client.jetstream().publish.side_effect = error
    result = send()
    assert result["success"] is False
    assert "acknowledgement" in result["error"]
    assert "secret-token" not in str(result) + caplog.text
    client.close.assert_awaited_once()


def test_retry_reuses_payload_and_id(client):
    client.jetstream().publish.side_effect = [TimeoutError(), SimpleNamespace(stream=CONFIG["stream"], seq=1)]
    policy = SimpleNamespace(retry_attempts=2, retry_backoff_seconds=0, is_quiet_now=lambda: False)
    assert send(policy=policy)["success"] is True
    calls = client.jetstream().publish.call_args_list
    assert calls[0] == calls[1]
    assert client.close.await_count == 2


def test_retry_is_bounded(client):
    client.jetstream().publish.side_effect = TimeoutError()
    policy = SimpleNamespace(retry_attempts=999, retry_backoff_seconds=0, is_quiet_now=lambda: False)
    assert send(policy=policy)["success"] is False
    assert client.jetstream().publish.await_count == 3


def test_quiet_hours_and_bypass(client):
    policy = SimpleNamespace(retry_attempts=0, retry_backoff_seconds=0, is_quiet_now=lambda: True)
    assert send(policy=policy) == {"success": False, "skipped": "quiet_hours"}
    client.connect.assert_not_awaited()
    assert send(policy=policy, bypass_quiet_hours=True)["success"] is True


def test_explicit_delivery_metadata_survives_separate_calls(client):
    kwargs = {"message_id": "price-alert:1:2:stable", "occurred_at": datetime(2026, 1, 2, 3, 4, 5)}
    assert send(**kwargs)["success"]
    assert send(**kwargs)["success"]
    calls = client.jetstream().publish.call_args_list
    assert calls[0] == calls[1]
    assert json.loads(calls[0].args[1])["timestamp"] == "2026-01-02T03:04:05Z"


def test_distinct_logical_sends_get_distinct_ids(client):
    send()
    send()
    calls = client.jetstream().publish.call_args_list
    assert calls[0].kwargs["headers"]["Nats-Msg-Id"] != calls[1].kwargs["headers"]["Nats-Msg-Id"]


@pytest.mark.parametrize("override", [
    {"server_url": "tls://secret-token@host:4222"}, {"server_url": "nats://host:4222"},
    {"server_url": "https://host:4222"}, {"server_url": "tls://host:4222/?token=secret-token"},
    {"subject": "a.*"}, {"subject": "a..b"}, {"subject": "a\r\nb"}, {"stream": "a.b"},
    {"token": "secret-token"}, {"token_env": "not a name"}, {"password_env": "PASS"},
    {"token_env": "TOKEN", "username": "user", "password_env": "PASS"},
    {"timeout_seconds": "nan"}, {"timeout_seconds": "11"}, {"timeout_seconds": "0"}, {"timeout_seconds": 5},
])
def test_bad_config_rejected_before_network_and_storage(client, override):
    config = CONFIG | override
    with pytest.raises(ValueError):
        send(config)
    client.connect.assert_not_awaited()
    with pytest.raises(HTTPException) as exc:
        validate_channel_config("nats_jetstream", config)
    assert exc.value.status_code == 400
    assert "secret-token" not in str(exc.value)


def test_missing_env_safe_actionable_error(client, monkeypatch):
    monkeypatch.delenv("PANWATCH_TEST_MISSING", raising=False)
    result = send(CONFIG | {"token_env": "PANWATCH_TEST_MISSING"})
    assert "token_env references a missing or empty" in result["error"]
    client.connect.assert_not_awaited()


def test_tls_and_auth_not_in_payload(client, monkeypatch):
    monkeypatch.setenv("PANWATCH_TEST_TOKEN", "secret-token")
    assert send(CONFIG | {"token_env": "PANWATCH_TEST_TOKEN"})["success"]
    options = client.connect.call_args.kwargs
    assert options["token"] == "secret-token"
    assert options["tls"].verify_mode == ssl.CERT_REQUIRED
    assert options["tls"].check_hostname
    assert options["tls_handshake_first"] is True
    assert options["allow_reconnect"] is False
    assert "secret-token" not in str(client.jetstream().publish.call_args)
    asyncio.run(options["error_cb"](RuntimeError("secret-token")))


def test_bad_ca_is_redacted(client, monkeypatch):
    monkeypatch.setenv("PANWATCH_TEST_CA", "bad-secret-ca")
    result = send(CONFIG | {"tls_ca_env": "PANWATCH_TEST_CA"})
    assert "valid PEM CA" in result["error"]
    assert "bad-secret-ca" not in str(result)
    client.connect.assert_not_awaited()


def test_payload_size_fails_without_truncation(client):
    envelope = nats.notification_envelope("Title", "汉" * nats.MAX_PAYLOAD_BYTES)
    with pytest.raises(ValueError, match="256 KiB"):
        asyncio.run(nats.publish_notification(CONFIG, envelope))
    client.connect.assert_not_awaited()


def test_cancel_closes_connection(client):
    client.jetstream().publish.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        send()
    client.close.assert_awaited_once()


def test_connect_failure_closes_connection(client):
    client.connect.side_effect = RuntimeError("secret-token")
    result = send()
    assert result["success"] is False
    assert "secret-token" not in str(result)
    client.close.assert_awaited_once()


def test_loopback_without_credentials(client):
    assert send(CONFIG | {"server_url": "nats://127.0.0.1:4222"})["success"]
    assert "tls" not in client.connect.call_args.kwargs


def test_registry_and_invalid_identifier(client):
    assert set(CHANNEL_TYPES["nats_jetstream"]["fields"]) == set(nats.CONFIG_FIELDS)
    assert send(message_id="bad\r\nheader")["success"] is False
    client.connect.assert_not_awaited()


def test_subject_fanout_has_distinct_broker_deduplication_ids(client):
    manager = NotifierManager()
    manager.add_channel("nats_jetstream", CONFIG)
    manager.add_channel("nats_jetstream", CONFIG | {"subject": "panwatch.other.v1"})
    assert asyncio.run(manager.notify_with_result("Title", "Content"))["success"]
    first, second = client.jetstream().publish.call_args_list
    assert json.loads(first.args[1])["message_id"] == json.loads(second.args[1])["message_id"]
    assert first.kwargs["headers"]["Nats-Msg-Id"] != second.kwargs["headers"]["Nats-Msg-Id"]


def test_channel_api_create_update_and_read_only_store_env_names(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from src.platform.persistence.database import Base
    from src.modules.administration.api.channels import (
        ChannelCreate, ChannelUpdate, ChannelResponse, create_channel, update_channel, list_channels,
    )
    monkeypatch.setenv("PANWATCH_TEST_TOKEN", "never-serialize-this-secret")
    engine = create_engine(f"sqlite:///{tmp_path / 'channels.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            row = create_channel(ChannelCreate(name="Bus", type="nats_jetstream", config=CONFIG | {"token_env": "PANWATCH_TEST_TOKEN"}), db)
            assert row.type == "nats_jetstream"
            serialized = ChannelResponse.model_validate(list_channels(db)[0]).model_dump_json()
            assert "PANWATCH_TEST_TOKEN" in serialized
            assert "never-serialize-this-secret" not in serialized
            with pytest.raises(HTTPException):
                update_channel(row.id, ChannelUpdate(config=CONFIG | {"token": "forbidden-secret"}), db)
            db.refresh(row)
            assert "token" not in row.config
            updated = update_channel(row.id, ChannelUpdate(config=CONFIG | {"subject": "panwatch.updated.v1"}), db)
            assert updated.config["subject"] == "panwatch.updated.v1"
            with pytest.raises(HTTPException):
                create_channel(ChannelCreate(name="Bad", type="nats_jetstream", config=CONFIG | {"password": "forbidden-secret"}), db)
            assert len(list_channels(db)) == 1
    finally:
        engine.dispose()


def test_selfcheck_detects_invalid_nats_configuration():
    from src.modules.administration.selfcheck import probe_notify_channel
    channel = SimpleNamespace(id=1, name="Bus", type="nats_jetstream", config=CONFIG | {"token": "never-log-this"})
    result = asyncio.run(probe_notify_channel(channel, send=False))
    assert result["status"] == "fail"
    assert "never-log-this" not in str(result)
