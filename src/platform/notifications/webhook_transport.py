"""Strict HTTP boundary missing from Apprise Custom JSON's public API.

No generic method/header/template configuration lives here. See
``docs/notifications/custom-rest.md`` for the version-tested gaps that require
this exception. PanWatch owns validation, credentials, payload and retries.
"""
from __future__ import annotations

import asyncio
import logging
import math
import ssl
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from src.platform.observability.log_context import redact_http_logs

logger = logging.getLogger(__name__)
MAX_RETRY_AFTER = 30.0


class PublishError(RuntimeError):
    """Safe delivery outcome; never carry URLs, responses or credentials."""
    def __init__(self, message: str, *, retryable: bool, retry_after: float = 0):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


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


async def post_notification(endpoint: str, payload: bytes, *, headers: dict[str, str],
                            context: ssl.SSLContext, timeout: float) -> None:
    """One cancellable POST, no redirects, ambient trust or response-body reads.

    The caller owns retries. A 2xx acknowledges receiver acceptance, not delivery
    to a downstream service. Only status and bounded Retry-After escape here.
    """
    try:
        # Redact this task's HTTP diagnostics before every handler, including
        # the DEBUG database sink, without muting concurrent provider requests.
        with redact_http_logs():
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(verify=context, trust_env=False, follow_redirects=False,
                                             timeout=timeout) as client:
                    async with client.stream("POST", endpoint, content=payload, headers=headers) as response:
                        status = response.status_code
                        logger.debug("REST gateway returned HTTP %d", status)
                        if 200 <= status < 300:
                            return
                        raise PublishError(f"REST gateway returned HTTP {status}",
                                           retryable=status == 429 or 500 <= status < 600,
                                           retry_after=retry_after_seconds(response.headers.get("Retry-After")) if status == 429 else 0)
    except PublishError:
        raise
    except (httpx.RequestError, TimeoutError):
        raise PublishError("REST request failed: check connectivity, TLS trust and timeout", retryable=True) from None
    except Exception:
        raise PublishError("REST request failed: check gateway configuration", retryable=False) from None
