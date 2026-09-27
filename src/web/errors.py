"""Stable error payloads for API responses consumed by the web client."""

from fastapi import HTTPException


def api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    """Build an HTTP error with a stable machine-readable code.

    The response wrapper exposes ``code`` as ``error_code`` while preserving the
    message for Chinese clients and diagnostics. Frontends should translate the
    stable code instead of matching prose.
    """
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=headers,
    )
