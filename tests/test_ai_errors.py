"""AI provider errors are useful to users only after safe classification."""

import httpx
from openai import AuthenticationError, BadRequestError, RateLimitError

from src.platform.ai.errors import classify_ai_service_error, diagnostic_ai_error_message
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


def test_content_filter_exposes_bounded_provider_diagnostic():
    rejected = _provider_error(
        BadRequestError,
        400,
        "request rejected",
        {
            "contentFilter": [{"level": 1, "role": "assistant"}],
            "error": {
                "code": "1301",
                "message": "系统检测到输入或生成内容可能包含不安全或敏感内容",
            },
        },
    )

    assert classify_ai_service_error(rejected).code == "ai_content_rejected"
    detail = diagnostic_ai_error_message(rejected)
    assert "HTTP 400" in detail
    assert "code=1301" in detail
    assert "不安全或敏感内容" in detail
    assert "contentFilter" not in detail


def test_provider_diagnostic_redacts_secrets_and_ignores_other_body_fields():
    invalid = _provider_error(
        BadRequestError,
        400,
        "request rejected",
        {"error": {"code": "bad_parameter", "message": (
            "Unsupported request api_key=private-key Authorization: Bearer private-token "
            "invalid api key sk-secret-value"
        )}, "prompt": "private prompt", "headers": {"Authorization": "private-header"}},
    )
    detail = diagnostic_ai_error_message(invalid)
    assert "Unsupported request" in detail
    assert "code=bad_parameter" in detail
    for secret in ("private-key", "private-token", "sk-secret-value", "private prompt", "private-header"):
        assert secret not in detail


def test_unstructured_error_diagnostic_is_bounded_and_keeps_timeout_reason():
    from openai import APITimeoutError

    timed_out = APITimeoutError(_REQUEST)
    assert "Request timed out" in diagnostic_ai_error_message(timed_out)
    invalid = _provider_error(BadRequestError, 400, "bad parameter", {"message": "x" * 3000})
    assert len(diagnostic_ai_error_message(invalid)) < 1000
