"""Explicit public URL prefix handling, independent of reverse-proxy headers."""

import re
from typing import Literal

from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send


ProxyMode = Literal["preserve", "strip"]
_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+\Z")


def normalize_base_path(value: str) -> str:
    """Return '' for root, otherwise a safe absolute path without a final slash.

    Deliberately reject encoded separators, dot segments, URLs and ambiguous
    slashes instead of attempting to repair deployment configuration.
    """
    if value in ("", "/"):
        return ""
    if not value.startswith("/"):
        raise ValueError("PANWATCH_BASE_PATH must be an absolute path, e.g. /apps/panwatch")
    path = value[:-1] if value.endswith("/") else value
    if any(part in ("", ".", "..") or not _SEGMENT.fullmatch(part) for part in path[1:].split("/")):
        raise ValueError("PANWATCH_BASE_PATH must contain only safe, non-empty URL path segments")
    return path


def public_path(path: str, base_path: str | None = None) -> str:
    """Prefix a trusted app-local absolute path, never a URL or network path."""
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise ValueError("expected an app-local absolute path")
    if base_path is None:
        from src.platform.runtime.config import Settings

        base_path = Settings().panwatch_base_path
    return normalize_base_path(base_path) + path


def app_path(scope: Scope) -> str:
    """App-local path for middleware; avoid Starlette's private helper API."""
    path = scope.get("path", "")
    prefix = scope.get("root_path", "")
    if prefix and (path == prefix or path.startswith(prefix + "/")):
        return path[len(prefix):]
    return path


class PublicPathMiddleware:
    """Adapt one explicit ingress mode to canonical ASGI paths.

    preserve: the upstream request still has the prefix (Caddy handle).
    strip: the trusted proxy removed it (Caddy handle_path).
    Never infer the mode or prefix from client-controlled forwarded headers.
    Keep canonical path + root_path so routing and generated URLs agree.
    """

    def __init__(self, app: ASGIApp, base_path: str = "", proxy_mode: ProxyMode = "preserve"):
        self.app = app
        self.base_path = normalize_base_path(base_path)
        if proxy_mode not in ("preserve", "strip"):
            raise ValueError("PANWATCH_PROXY_MODE must be preserve or strip")
        self.proxy_mode = proxy_mode

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        scope = dict(scope)
        prefix = self.base_path
        path = scope.get("path", "/")
        if prefix and self.proxy_mode == "preserve":
            if path == prefix and scope["type"] == "http":
                # A relative Location avoids trusting Host/Forwarded for redirects.
                query = scope.get("query_string", b"").decode("latin-1")
                location = prefix + "/" + ("?" + query if query else "")
                await RedirectResponse(location, status_code=308)(scope, receive, send)
                return
            if not path.startswith(prefix + "/"):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
                return
        elif prefix:
            scope["path"] = prefix + path
            scope["raw_path"] = prefix.encode("ascii") + scope.get("raw_path", path.encode("utf-8"))

        # Configuration is authoritative, including when deployed at root.
        scope["root_path"] = prefix
        scope["app_root_path"] = prefix
        await self.app(scope, receive, send)
