"""Outbound-only JetStream publisher. Broker resources are operator-managed."""
from __future__ import annotations

import asyncio
import json
import hashlib
import math
import os
import re
import ssl
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

from nats.aio.client import Client

MAX_PAYLOAD_BYTES = 256 * 1024
CONFIG_FIELDS = (
    "server_url", "subject", "stream", "token_env", "username", "password_env",
    "tls_ca_env", "timeout_seconds",
)


def validate_config(config: dict) -> dict:
    """Validate before persistence; never include input values in errors."""
    if not isinstance(config, dict) or set(config) - set(CONFIG_FIELDS):
        raise ValueError("NATS configuration contains unsupported fields; use environment references for secrets")
    if any(not isinstance(value, str) for value in config.values()):
        raise ValueError("NATS configuration values must be strings")
    cfg = {key: value.strip() for key, value in config.items()}
    try:
        url = urlsplit(cfg.get("server_url", ""))
        port = url.port
        valid = (url.scheme in {"tls", "nats"} and url.hostname and port
                 and url.username is None and url.password is None
                 and not url.path and not url.query and not url.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("NATS server_url must be tls://host:port without credentials or a path")
    cfg["server_url"] = url.geturl()  # normalize scheme for the client as well
    if url.scheme == "nats" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("NATS requires verified TLS except for local loopback development")
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", cfg.get("subject", "")):
        raise ValueError("NATS subject must be a concrete subject without wildcards")
    if len(cfg["subject"]) > 255:
        raise ValueError("NATS subject is too long")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", cfg.get("stream", "")):
        raise ValueError("NATS stream must be a valid pre-provisioned stream name")
    for key in ("token_env", "password_env", "tls_ca_env"):
        if cfg.get(key) and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", cfg[key]):
            raise ValueError(f"NATS {key} must be an environment variable name, not its value")
    if bool(cfg.get("username")) != bool(cfg.get("password_env")):
        raise ValueError("NATS username and password_env must be supplied together")
    if cfg.get("token_env") and cfg.get("username"):
        raise ValueError("NATS token and username/password authentication cannot be combined")
    if url.scheme != "tls" and any(cfg.get(key) for key in ("token_env", "password_env", "tls_ca_env")):
        raise ValueError("NATS authentication and custom CA configuration require TLS")
    try:
        timeout = float(cfg.get("timeout_seconds") or "5")
        if not math.isfinite(timeout) or not 1 <= timeout <= 10:
            raise ValueError
    except ValueError:
        raise ValueError("NATS timeout_seconds must be between 1 and 10") from None
    return cfg


def _env(config: dict, key: str) -> str | None:
    name = config.get(key)
    if not name:
        return None
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"NATS {key} references a missing or empty environment variable")
    return value


def notification_envelope(title: str, content: str, *, message_id: str | None = None,
                          occurred_at: datetime | None = None) -> dict:
    identifier = message_id if message_id is not None else uuid4().hex
    # Also goes into a NATS header: reject line breaks/control characters.
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,256}", identifier):
        raise ValueError("NATS message_id must be a nonempty safe identifier (maximum 256 characters)")
    timestamp = occurred_at if occurred_at is not None else datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)  # database timestamps are UTC
    def clean(value: str) -> str:
        # Preserve Unicode and formatting; strip non-printing controls except LF/tab.
        return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)
    return {"schema_version": 1, "message_id": identifier,
            "timestamp": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "source": "PanWatch", "title": clean(title), "content": clean(content)}


def publication_id(config: dict, message_id: str) -> str:
    # Deduplication is stream-scoped, not subject-scoped. Keep routing fan-out.
    identity = json.dumps([config["stream"], config["subject"], message_id], separators=(",", ":"))
    return "panwatch-v1-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


async def publish_notification(config: dict, envelope: dict) -> None:
    """Return only after an acknowledged write to the expected stream.

    No automatic reconnect/retry here: the caller owns bounded policy retries
    and reuses the envelope ID. Suppress provider exception text (may contain
    auth data). Cancellation still propagates after closing the socket.
    """
    cfg = validate_config(config)
    timeout = float(cfg.get("timeout_seconds") or "5")
    try:
        payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, TypeError):
        raise ValueError("NATS notification cannot be encoded as UTF-8 JSON") from None
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("NATS notification exceeds the 256 KiB payload limit")
    options = {}
    token = _env(cfg, "token_env")
    password = _env(cfg, "password_env")
    ca = _env(cfg, "tls_ca_env")
    if token:
        options["token"] = token
    if password:
        options.update(user=cfg["username"], password=password)
    if urlsplit(cfg["server_url"]).scheme == "tls":
        try:
            options["tls"] = ssl.create_default_context(cadata=ca)
            # INFO-negotiated TLS can downgrade before CONNECT/auth. Require
            # an encrypted connection before accepting any server INFO.
            options["tls_handshake_first"] = True
        except (ValueError, ssl.SSLError):
            raise ValueError("NATS tls_ca_env must reference a valid PEM CA certificate") from None

    async def on_error(_error):
        # nats-py's default callback logs raw provider errors.
        pass

    client = Client()
    try:
        async def send():
            await client.connect(servers=[cfg["server_url"]], name="PanWatch-publisher",
                                 connect_timeout=timeout, allow_reconnect=False,
                                 max_reconnect_attempts=0, error_cb=on_error, **options)
            ack = await client.jetstream(timeout=timeout).publish(
                cfg["subject"], payload, timeout=timeout, stream=cfg["stream"],
                headers={"Nats-Msg-Id": publication_id(cfg, envelope["message_id"]), "Content-Type": "application/json"},
            )
            if not ack or ack.stream != cfg["stream"] or not ack.seq or ack.seq < 1:
                raise RuntimeError("invalid acknowledgement")
        await asyncio.wait_for(send(), timeout=timeout * 2)
    except Exception:
        raise RuntimeError("NATS publish failed: check connectivity, TLS/auth, subject permissions and the pre-provisioned JetStream stream; no valid acknowledgement received") from None
    finally:
        # Acknowledged writes need no drain. Close even after partial connection,
        # failures or cancellation. Never let cleanup expose provider text.
        try:
            await asyncio.wait_for(client.close(), timeout=1)
        except Exception:
            pass
