"""Stable error payloads for API responses consumed by the web client."""

from fastapi import HTTPException

from src.platform.ai.errors import classify_ai_service_error


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


def ai_api_error(
    error: BaseException,
    *,
    status_code: int | None = None,
) -> HTTPException:
    """Convert an AI provider exception to a safe, translatable API error."""
    descriptor = classify_ai_service_error(error)
    return api_error(
        status_code or descriptor.status_code,
        descriptor.code,
        descriptor.message,
    )
