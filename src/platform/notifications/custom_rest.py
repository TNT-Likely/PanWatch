"""Small outbound HTTP contract; the receiving gateway owns durable delivery."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import ssl
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

MAX_PAYLOAD_BYTES = 256 * 1024
MAX_RETRY_AFTER = 30.0
CONFIG_FIELDS = ("endpoint_url", "token_env", "tls_ca_env", "timeout_seconds")


class PublishError(RuntimeError):
    """Safe error information; never carry URLs, responses or credentials."""
    def __init__(self, message: str, *, retryable: bool, retry_after: float = 0):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict) or set(config) - set(CONFIG_FIELDS):
        raise ValueError("REST configuration contains unsupported fields; use environment references for secrets")
    if any(not isinstance(value, str) for value in config.values()):
        raise ValueError("REST configuration values must be strings")
    cfg = {key: value.strip() for key, value in config.items()}
    endpoint = cfg.get("endpoint_url", "")
    try:
        url = urlsplit(endpoint)
        port = url.port
        valid = (url.scheme in {"http", "https"} and url.hostname
                 and url.username is None and url.password is None
                 and not url.query and not url.fragment and "\\" not in endpoint
                 and not re.search(r"[\s\x00-\x1f\x7f]", endpoint)
                 and (port is None or port > 0))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("REST endpoint_url must be an HTTP(S) URL without credentials, query or fragment")
    cfg["endpoint_url"] = url.geturl()
    if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("REST requires verified HTTPS except for explicit local loopback development")
    for key in ("token_env", "tls_ca_env"):
        if cfg.get(key) and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", cfg[key]):
            raise ValueError(f"REST {key} must be an environment variable name, not its value")
    if url.scheme != "https" and any(cfg.get(key) for key in ("token_env", "tls_ca_env")):
        raise ValueError("REST authentication and custom CA configuration require HTTPS")
    try:
        timeout = float(cfg.get("timeout_seconds") or "5")
        if not math.isfinite(timeout) or not 1 <= timeout <= 10:
            raise ValueError
    except ValueError:
        raise ValueError("REST timeout_seconds must be between 1 and 10") from None
    return cfg


def _env(config: dict, key: str) -> str | None:
    name = config.get(key)
    if not name:
        return None
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"REST {key} references a missing or empty environment variable")
    return value


def notification_envelope(title: str, content: str, *, message_id: str | None = None,
                          occurred_at: datetime | None = None) -> dict:
    identifier = message_id if message_id is not None else uuid4().hex
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,256}", identifier):
        raise ValueError("REST message_id must be a nonempty safe identifier (maximum 256 characters)")
    timestamp = occurred_at if occurred_at is not None else datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    def clean(value: str) -> str:
        return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)
    return {"schema_version": 1, "event_id": identifier,
            "timestamp": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "source": "PanWatch", "title": clean(title), "content": clean(content),
            "format": "markdown"}


def publication_id(envelope: dict) -> str:
    identity = json.dumps([envelope["source"], envelope["event_id"]], separators=(",", ":"))
    return "panwatch-v1-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0
    try:
        delay = float(value)
        if not math.isfinite(delay):
            return 0
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            delay = (date - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0
    return min(MAX_RETRY_AFTER, max(0, delay))


async def publish_notification(config: dict, envelope: dict) -> None:
    """One bounded attempt. 2xx acknowledges gateway acceptance, not Telegram.

    No redirects, proxy/environment credentials, implicit transport retries or
    response-body reads. The caller owns retries and keeps the envelope stable.
    """
    cfg = validate_config(config)
    timeout = float(cfg.get("timeout_seconds") or "5")
    try:
        payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, TypeError):
        raise ValueError("REST notification cannot be encoded as UTF-8 JSON") from None
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("REST notification exceeds the 256 KiB payload limit")
    token = _env(cfg, "token_env")
    if token and not re.fullmatch(r"[A-Za-z0-9\-._~+/]+=*", token):
        raise ValueError("REST token_env must reference a valid bearer token")
    ca = _env(cfg, "tls_ca_env")
    try:
        context = ssl.create_default_context(cadata=ca)
    except (ValueError, ssl.SSLError):
        raise ValueError("REST tls_ca_env must reference a valid PEM CA certificate") from None
    headers = {"Content-Type": "application/json", "Idempotency-Key": publication_id(envelope)}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(verify=context, trust_env=False, follow_redirects=False,
                                         timeout=timeout) as client:
                async with client.stream("POST", cfg["endpoint_url"], content=payload, headers=headers) as response:
                    if 200 <= response.status_code < 300:
                        return
                    status = response.status_code
                    raise PublishError(f"REST gateway returned HTTP {status}",
                                       retryable=status == 429 or 500 <= status < 600,
                                       retry_after=retry_after_seconds(response.headers.get("Retry-After")) if status == 429 else 0)
    except PublishError:
        raise
    except (httpx.RequestError, TimeoutError):
        raise PublishError("REST request failed: check connectivity, TLS trust and timeout", retryable=True) from None
    except Exception:
        raise PublishError("REST request failed: check gateway configuration", retryable=False) from None
