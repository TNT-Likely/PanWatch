"""Safe, stable user-facing classification for AI provider failures.

Provider exceptions often contain useful diagnostics, but their raw text can also
include request fragments, endpoints, or account details.  This module keeps the
raw exception available to server logs. HTTP errors use stable reviewed messages;
AgentRun diagnostics add bounded, redacted provider codes and messages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)


@dataclass(frozen=True)
class AIErrorDescriptor:
    code: str
    message: str
    status_code: int
    retryable: bool


_ERRORS: dict[str, AIErrorDescriptor] = {
    "ai_quota_exhausted": AIErrorDescriptor(
        code="ai_quota_exhausted",
        message="AI 服务额度已用尽，请充值或切换可用模型后重试。",
        status_code=429,
        retryable=False,
    ),
    "ai_rate_limited": AIErrorDescriptor(
        code="ai_rate_limited",
        message="AI 服务请求过于频繁，请稍后重试或切换模型。",
        status_code=429,
        retryable=True,
    ),
    "ai_authentication_failed": AIErrorDescriptor(
        code="ai_authentication_failed",
        message="AI 服务认证失败，请检查 API Key 是否正确且仍然有效。",
        status_code=502,
        retryable=False,
    ),
    "ai_permission_denied": AIErrorDescriptor(
        code="ai_permission_denied",
        message="当前 API Key 无权使用该 AI 模型，请检查服务商权限或切换模型。",
        status_code=502,
        retryable=False,
    ),
    "ai_model_unavailable": AIErrorDescriptor(
        code="ai_model_unavailable",
        message="配置的 AI 模型不存在或暂不可用，请检查模型名称或切换模型。",
        status_code=502,
        retryable=False,
    ),
    "ai_context_limit_exceeded": AIErrorDescriptor(
        code="ai_context_limit_exceeded",
        message="对话内容超过模型上下文限制，请新建会话、压缩上下文或切换更大上下文模型。",
        status_code=400,
        retryable=False,
    ),
    "ai_content_rejected": AIErrorDescriptor(
        code="ai_content_rejected",
        message="AI 服务拒绝处理本次内容，请调整问题后重试。",
        status_code=400,
        retryable=False,
    ),
    "ai_request_invalid": AIErrorDescriptor(
        code="ai_request_invalid",
        message="AI 服务无法接受当前请求，请检查模型兼容性或相关配置。",
        status_code=400,
        retryable=False,
    ),
    "ai_request_timeout": AIErrorDescriptor(
        code="ai_request_timeout",
        message="AI 服务响应超时，请稍后重试或切换模型。",
        status_code=504,
        retryable=True,
    ),
    "ai_connection_failed": AIErrorDescriptor(
        code="ai_connection_failed",
        message="无法连接 AI 服务，请检查服务地址、代理和网络后重试。",
        status_code=502,
        retryable=True,
    ),
    "ai_service_unavailable": AIErrorDescriptor(
        code="ai_service_unavailable",
        message="AI 服务暂时不可用，请稍后重试或切换模型。",
        status_code=503,
        retryable=True,
    ),
    "ai_service_failed": AIErrorDescriptor(
        code="ai_service_failed",
        message="AI 服务调用失败，请检查模型配置或稍后重试。",
        status_code=502,
        retryable=True,
    ),
}


class AIServiceError(RuntimeError):
    """Typed provider failure that can cross the neutral agent runtime safely."""

    def __init__(self, descriptor: AIErrorDescriptor):
        super().__init__(descriptor.message)
        self.code = descriptor.code
        # PanAgent deliberately recognizes this generic attribute without
        # importing host-specific exception types.
        self.error_code = descriptor.code
        self.status_code = descriptor.status_code
        self.retryable = descriptor.retryable


def descriptor_for_code(code: str) -> AIErrorDescriptor:
    return _ERRORS.get(code, _ERRORS["ai_service_failed"])


def _flatten_body(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten_body(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_body(item) for item in value)
    return str(value)


def _matches(text: str, *markers: str) -> bool:
    return any(marker in text for marker in markers)


def classify_ai_service_error(error: BaseException) -> AIErrorDescriptor:
    """Classify an exception without returning its untrusted provider text."""
    if isinstance(error, AIServiceError):
        return descriptor_for_code(error.code)

    body = _flatten_body(getattr(error, "body", None))
    provider_code = str(getattr(error, "code", "") or "")
    text = " ".join((str(error), body, provider_code)).lower()
    status = getattr(error, "status_code", None)

    if _matches(
        text,
        "insufficient_quota",
        "quota_exceeded",
        "billing_hard_limit",
        "billing limit",
        "exceeded your current quota",
        "credit balance",
        "credits exhausted",
        "insufficient balance",
        "insufficient credit",
        "no credits",
        "account balance",
        "余额不足",
        "额度不足",
        "额度已用尽",
    ):
        return _ERRORS["ai_quota_exhausted"]
    if isinstance(error, RateLimitError) or status == 429:
        return _ERRORS["ai_rate_limited"]
    if isinstance(error, AuthenticationError) or status == 401 or _matches(
        text,
        "invalid api key",
        "incorrect api key",
        "api key is invalid",
        "unauthorized",
    ):
        return _ERRORS["ai_authentication_failed"]
    if isinstance(error, PermissionDeniedError) or status == 403 or _matches(
        text,
        "permission denied",
        "access denied",
        "not allowed to use this model",
    ):
        return _ERRORS["ai_permission_denied"]
    if (
        isinstance(error, APITimeoutError)
        or isinstance(error, TimeoutError)
        or _matches(text, "request timed out", "read timeout", "connect timeout")
    ):
        return _ERRORS["ai_request_timeout"]
    if isinstance(error, APIConnectionError) or _matches(
        text,
        "connection refused",
        "connection error",
        "failed to connect",
        "name or service not known",
    ):
        return _ERRORS["ai_connection_failed"]

    if _matches(
        text,
        "context_length_exceeded",
        "maximum context length",
        "context window",
        "too many tokens",
        "prompt is too long",
        "上下文长度",
    ):
        return _ERRORS["ai_context_limit_exceeded"]
    if _matches(
        text,
        "content_policy",
        "contentfilter",
        "content filter",
        "content_filter",
        "safety policy",
        "moderation",
        "sensitive content",
    ):
        return _ERRORS["ai_content_rejected"]
    if isinstance(error, NotFoundError) or status == 404 or _matches(
        text,
        "model_not_found",
        "model not found",
        "no such model",
        "does not exist or you do not have access",
    ):
        return _ERRORS["ai_model_unavailable"]
    if isinstance(error, BadRequestError) or status in {400, 422}:
        return _ERRORS["ai_request_invalid"]
    if isinstance(error, InternalServerError) or (
        isinstance(status, int) and status >= 500
    ):
        return _ERRORS["ai_service_unavailable"]

    return _ERRORS["ai_service_failed"]


def as_ai_service_error(error: BaseException) -> AIServiceError:
    if isinstance(error, AIServiceError):
        return error
    return AIServiceError(classify_ai_service_error(error))


def safe_ai_error_message(error: BaseException) -> str:
    return classify_ai_service_error(error).message


_SECRET_DETAIL_RE = re.compile(
    r"(?i)\b(api[_ -]?key|authorization|access[_ -]?token|password|secret|cookie)"
    r"[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|(?:bearer\s+)?[^\s,;]+)"
)


def _diagnostic_text(value: Any, limit: int) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    text = _SECRET_DETAIL_RE.sub(r"\1=[redacted]", str(value))
    text = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+", "Bearer [redacted]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", text)
    return " ".join(text.split())[:limit]


def diagnostic_ai_error_message(error: BaseException) -> str:
    """Return a bounded provider diagnostic suitable for an AgentRun/UI.

    The normal error message remains stable and translated. Agent progress needs
    enough provider detail to distinguish content filters, invalid requests and
    gateway failures, but must not expose headers, keys, prompts or a full
    exception traceback.
    """
    descriptor = classify_ai_service_error(error)
    body = getattr(error, "body", None)
    nested = body.get("error") if isinstance(body, dict) else None
    if not isinstance(nested, dict):
        nested = {}

    provider_code = (
        nested.get("code")
        or (body.get("code") if isinstance(body, dict) else None)
        or getattr(error, "code", None)
    )
    provider_message = nested.get("message") or (
        body.get("message") if isinstance(body, dict) else None
    )
    if not provider_message and isinstance(body, dict) and isinstance(body.get("error"), str):
        provider_message = body["error"]
    if not provider_message and not isinstance(body, dict):
        provider_message = getattr(error, "message", None) or str(error)

    status = getattr(error, "status_code", None)
    parts: list[str] = []
    if isinstance(status, int):
        parts.append(f"HTTP {status}")
    code = _diagnostic_text(provider_code, 64)
    if code:
        parts.append(f"code={code}")
    detail = _diagnostic_text(provider_message, 800)
    if detail and detail != descriptor.message:
        parts.append(detail)

    if not parts:
        return descriptor.message
    return f"{descriptor.message}（{' · '.join(parts)}）"
