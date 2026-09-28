"""AI provider errors are useful to users only after safe classification."""

import httpx
from openai import AuthenticationError, BadRequestError, RateLimitError

from src.platform.ai.errors import classify_ai_service_error
from src.web.errors import ai_api_error


_REQUEST = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")


def _provider_error(cls, status: int, message: str, body=None):
    return cls(
        message,
        response=httpx.Response(status, request=_REQUEST),
        body=body,
    )


def test_quota_exhaustion_is_distinct_from_transient_rate_limiting():
    quota = _provider_error(
        RateLimitError,
        429,
        "You exceeded your current quota",
        {"error": {"code": "insufficient_quota"}},
    )
    rate_limit = _provider_error(RateLimitError, 429, "Requests per minute exceeded")

    assert classify_ai_service_error(quota).code == "ai_quota_exhausted"
    assert classify_ai_service_error(quota).retryable is False
    assert classify_ai_service_error(rate_limit).code == "ai_rate_limited"
    assert classify_ai_service_error(rate_limit).retryable is True


def test_authentication_and_context_failures_have_actionable_codes():
    auth = _provider_error(AuthenticationError, 401, "invalid api key")
    context = _provider_error(
        BadRequestError,
        400,
        "maximum context length exceeded",
        {"error": {"code": "context_length_exceeded"}},
    )

    assert classify_ai_service_error(auth).code == "ai_authentication_failed"
    assert classify_ai_service_error(context).code == "ai_context_limit_exceeded"


def test_api_error_never_exposes_raw_provider_text():
    raw = _provider_error(
        AuthenticationError,
        401,
        "invalid api key sk-secret-value",
    )

    response = ai_api_error(raw)

    assert response.status_code == 502
    assert response.detail["code"] == "ai_authentication_failed"
    assert "sk-secret-value" not in response.detail["message"]
