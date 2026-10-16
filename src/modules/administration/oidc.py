"""Optional single-owner OpenID Connect authentication.

The provider's tokens never reach the browser or persistent storage. Only hashed,
revocable local session identifiers are stored; each identity is issuer + subject.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt
from dotenv import dotenv_values
from fastapi import Request
from sqlalchemy.orm import Session

from src.platform.persistence.models import OIDCBrowserGeneration, OIDCLoginTransaction, OIDCSession
from src.platform.persistence.database import SessionLocal
from src.platform.observability.log_context import redact_http_logs
from src.web.errors import api_error

SESSION_COOKIE = "panwatch_oidc_session"
TRANSACTION_COOKIE = "panwatch_oidc_login"
TRANSACTION_SECONDS = 600
COOKIE_QUERY_BATCH = 32
MAX_COOKIE_DELETIONS = 16
# Explicit asymmetric allowlist; never derive allowed algorithms from token data.
SIGNING_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"]


class OIDCError(ValueError):
    """Configuration/protocol error, deliberately without provider response data."""


def _flag(values: dict, name: str, default: bool = False) -> bool:
    value = str(values.get(name, str(default))).strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise OIDCError(f"{name} must be true or false")
    return value in {"true", "1"}


def enabled() -> bool:
    # Invalid configuration must never silently enable guest/password access.
    try:
        return get_config() is not None
    except OIDCError:
        return True


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _valid_url(value: str, allow_local_http: bool, *, issuer: bool = False) -> str:
    if not isinstance(value, str):
        raise OIDCError("Invalid OIDC URL")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise OIDCError("Invalid OIDC URL") from exc
    local_http = allow_local_http and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise OIDCError("OIDC URLs require HTTPS (HTTP is opt-in for loopback development only)")
    if "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise OIDCError("Invalid OIDC URL")
    if any(part in {".", ".."} for part in parsed.path.split("/")) or "%" in parsed.path or "//" in parsed.path:
        raise OIDCError("OIDC URLs require an unambiguous path")
    # Browsers serialize origins with lower-case/IDNA hosts and no default port.
    # Keep issuer text exact for ID-token comparison; normalize redirect origins.
    if "%" in parsed.netloc:
        raise OIDCError("Encoded OIDC hostnames are not supported")
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise OIDCError("Invalid OIDC URL") from exc
    try:
        address = ipaddress.ip_address(url.host)
    except ValueError:
        # Browsers reinterpret short/octal/hex IPv4 forms. Reject these rather
        # than accepting a URL whose browser Origin differs from this server.
        last_label = url.host.rstrip(".").rsplit(".", 1)[-1]
        if last_label.isdigit() or last_label.lower().startswith("0x"):
            raise OIDCError("OIDC numeric hosts must use canonical IP literals")
    else:
        url = url.copy_with(host=address.compressed)
    return value if issuer else str(url).rstrip("/")


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    public_url: str
    subjects: frozenset[str]
    allow_password: bool
    allow_local_http: bool
    token_auth_method: str
    session_seconds: int
    endpoint_origins: frozenset[str]

    @property
    def callback_url(self) -> str:
        return self.public_url + "/api/auth/oidc/callback"

    @property
    def cookie_path(self) -> str:
        return urlsplit(self.public_url).path.rstrip("/") + "/"

    def cookie_name(self, name: str) -> str:
        # Root deployments retain their original names. Nested deployments must
        # not read an ancestor's cookie with the same name: browsers send both,
        # and parsing can otherwise select the older ancestor value.
        return name if self.cookie_path == "/" else f"{name}_{digest(self.cookie_path)[:16]}"

    @property
    def session_cookie(self) -> str:
        return self.cookie_name(SESSION_COOKIE)

    @property
    def transaction_cookie(self) -> str:
        return self.cookie_name(TRANSACTION_COOKIE)

    @property
    def origin(self) -> str:
        parsed = urlsplit(self.public_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def secure_cookie(self) -> bool:
        return urlsplit(self.public_url).scheme == "https"

    @property
    def binding(self) -> str:
        return digest(json.dumps([self.issuer, self.client_id, self.public_url], separators=(",", ":")))


@lru_cache(maxsize=1)
def get_config() -> OIDCConfig | None:
    values = {key: "" if value is None else value for key, value in {**dotenv_values(".env"), **os.environ}.items()}
    if not _flag(values, "OIDC_ENABLED"):
        return None
    allow_http = _flag(values, "OIDC_ALLOW_HTTP_LOCALHOST")
    issuer = _valid_url(values.get("OIDC_ISSUER", "").strip(), allow_http, issuer=True)
    public_url = _valid_url(values.get("OIDC_PUBLIC_URL", "").strip(), allow_http)
    base_path = values.get("PANWATCH_BASE_PATH", "").strip().rstrip("/")
    if urlsplit(public_url).path.rstrip("/") != base_path:
        raise OIDCError("OIDC_PUBLIC_URL path must match PANWATCH_BASE_PATH")
    client_id = values.get("OIDC_CLIENT_ID", "").strip()
    if not client_id:
        raise OIDCError("OIDC_CLIENT_ID is required")
    try:
        subjects = json.loads(values.get("OIDC_ALLOWED_SUBJECTS", "[]"))
    except (ValueError, TypeError) as exc:
        raise OIDCError("OIDC_ALLOWED_SUBJECTS must be a JSON array") from exc
    if not isinstance(subjects, list) or not subjects or any(not isinstance(sub, str) or not sub or len(sub) > 255 for sub in subjects):
        raise OIDCError("OIDC_ALLOWED_SUBJECTS must explicitly list allowed subject identifiers")
    method = values.get("OIDC_TOKEN_AUTH_METHOD", "client_secret_basic").strip()
    if method not in {"client_secret_basic", "client_secret_post", "none"}:
        raise OIDCError("Unsupported OIDC_TOKEN_AUTH_METHOD")
    secret = values.get("OIDC_CLIENT_SECRET", "")
    if method != "none" and not secret:
        raise OIDCError("OIDC_CLIENT_SECRET is required for confidential clients")
    try:
        hours = int(values.get("OIDC_SESSION_HOURS", "12"))
    except ValueError as exc:
        raise OIDCError("OIDC_SESSION_HOURS must be an integer") from exc
    if not 1 <= hours <= 168:
        raise OIDCError("OIDC_SESSION_HOURS must be between 1 and 168")
    issuer_parts = urlsplit(_valid_url(issuer, allow_http))
    origins = {f"{issuer_parts.scheme}://{issuer_parts.netloc}"}
    try:
        extra_origins = json.loads(values.get("OIDC_ALLOWED_ENDPOINT_ORIGINS", "[]"))
    except (ValueError, TypeError) as exc:
        raise OIDCError("OIDC_ALLOWED_ENDPOINT_ORIGINS must be a JSON array") from exc
    if not isinstance(extra_origins, list):
        raise OIDCError("OIDC_ALLOWED_ENDPOINT_ORIGINS must be a JSON array")
    for origin in extra_origins:
        normalized = _valid_url(origin, allow_http)
        if urlsplit(normalized).path:
            raise OIDCError("OIDC_ALLOWED_ENDPOINT_ORIGINS must contain origins without paths")
        origins.add(normalized)
    return OIDCConfig(issuer, client_id, secret, public_url, frozenset(subjects), _flag(values, "OIDC_ALLOW_PASSWORD_LOGIN"), allow_http, method, hours * 3600, frozenset(origins))


def require_config() -> OIDCConfig:
    try:
        config = get_config()
    except OIDCError:
        raise api_error(503, "oidc_not_configured", "单点登录配置无效，请联系管理员") from None
    if config is None:
        raise api_error(404, "oidc_disabled", "单点登录未启用")
    return config


def password_enabled() -> bool:
    config = require_config() if enabled() else None
    return config is None or config.allow_password


def http_client() -> httpx.Client:
    # No redirects: never forward client credentials to a redirected endpoint.
    return httpx.Client(timeout=httpx.Timeout(10.0), follow_redirects=False)


def _json_response(response: httpx.Response) -> dict:
    response.raise_for_status()
    if len(response.content) > 1_000_000:
        raise OIDCError("OIDC response too large")
    data = response.json()
    if not isinstance(data, dict):
        raise OIDCError("Invalid OIDC response")
    return data


def discover(config: OIDCConfig, client: httpx.Client) -> dict:
    metadata = _json_response(client.get(config.issuer.rstrip("/") + "/.well-known/openid-configuration"))
    if metadata.get("issuer") != config.issuer:
        raise OIDCError("OIDC discovery issuer mismatch")
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint = _valid_url(metadata.get(name, ""), config.allow_local_http)
        parts = urlsplit(endpoint)
        if f"{parts.scheme}://{parts.netloc}" not in config.endpoint_origins:
            raise OIDCError("Discovery endpoint origin is not explicitly trusted")
    if "code" not in metadata.get("response_types_supported", []):
        raise OIDCError("Provider does not support authorization code flow")
    methods = metadata.get("code_challenge_methods_supported")
    if methods is not None and "S256" not in methods:
        raise OIDCError("Provider does not support S256 PKCE")
    if config.token_auth_method not in metadata.get("token_endpoint_auth_methods_supported", ["client_secret_basic"]):
        raise OIDCError("Provider does not support configured client authentication")
    return metadata


def generation_cookie(base: str, browser_hash: str) -> str:
    return f"{base}_{browser_hash}"


def _cookie_generations(request: Request, config: OIDCConfig) -> set[str]:
    """Only this deployment's exact names; sibling/ancestor cookies do not match."""
    hashes = set()
    for name, value in request.cookies.items():
        for base in (config.transaction_cookie, config.session_cookie):
            prefix = base + "_"
            suffix = name[len(prefix):] if name.startswith(prefix) else ""
            if re.fullmatch(r"[0-9a-f]{64}", suffix) and 0 < len(value) <= 256:
                hashes.add(suffix)
    return hashes


def _generation_rows(db: Session, config: OIDCConfig, hashes: set[str]) -> list[OIDCBrowserGeneration]:
    # Delayed first-touch responses may temporarily leave many names. Batch the
    # lookup rather than making that state prevent status, logout, or recovery.
    values, rows = sorted(hashes), []
    for offset in range(0, len(values), COOKIE_QUERY_BATCH):
        rows.extend(db.query(OIDCBrowserGeneration).filter(
            OIDCBrowserGeneration.browser_hash.in_(values[offset:offset + COOKIE_QUERY_BATCH]),
            OIDCBrowserGeneration.config_binding == config.binding,
            OIDCBrowserGeneration.expires_at > int(time.time()),
        ).populate_existing().all())
    return rows


def browser_generations(db: Session, request: Request, config: OIDCConfig) -> list[OIDCBrowserGeneration]:
    hashes = _cookie_generations(request, config)
    if not hashes:
        return []
    rows = _generation_rows(db, config, hashes)
    return [row for row in rows if (
        digest(request.cookies.get(generation_cookie(config.transaction_cookie, row.browser_hash), "")) == row.browser_hash
        or (row.session_hash and digest(request.cookies.get(generation_cookie(config.session_cookie, row.browser_hash), "")) == row.session_hash)
    )]


def _has_binding_cookie(request: Request, config: OIDCConfig, row: OIDCBrowserGeneration) -> bool:
    value = request.cookies.get(generation_cookie(config.transaction_cookie, row.browser_hash), "")
    return bool(value and len(value) <= 256 and digest(value) == row.browser_hash)


def _has_session_cookie(request: Request, config: OIDCConfig, row: OIDCBrowserGeneration) -> bool:
    value = request.cookies.get(generation_cookie(config.session_cookie, row.browser_hash), "")
    return bool(value and row.session_hash and digest(value) == row.session_hash)


def _revoke(db: Session, ids: list[int]) -> None:
    if not ids:
        return
    rows = db.query(OIDCBrowserGeneration).filter(OIDCBrowserGeneration.id.in_(ids)).all()
    db.query(OIDCLoginTransaction).filter(OIDCLoginTransaction.browser_hash.in_([row.browser_hash for row in rows])).delete(synchronize_session=False)
    db.query(OIDCSession).filter(OIDCSession.session_hash.in_([row.session_hash for row in rows if row.session_hash])).delete(synchronize_session=False)
    db.query(OIDCBrowserGeneration).filter(OIDCBrowserGeneration.id.in_(ids)).update({"revoked": True}, synchronize_session=False)
    # Terminal attempts with no issued credential need only cover outstanding
    # transaction ordering. Issued sessions retain their full logout horizon.
    deadline = int(time.time()) + TRANSACTION_SECONDS
    db.query(OIDCBrowserGeneration).filter(OIDCBrowserGeneration.id.in_(ids),
        OIDCBrowserGeneration.session_hash.is_(None), OIDCBrowserGeneration.expires_at > deadline,
    ).update({"expires_at": deadline}, synchronize_session=False)


def cleanup(db: Session) -> None:
    now = int(time.time())
    db.query(OIDCLoginTransaction).filter(OIDCLoginTransaction.expires_at <= now).delete(synchronize_session=False)
    db.query(OIDCSession).filter(OIDCSession.expires_at <= now).delete(synchronize_session=False)
    db.query(OIDCBrowserGeneration).filter(OIDCBrowserGeneration.expires_at <= now).delete(synchronize_session=False)


def obsolete_cookies(db: Session, request: Request, config: OIDCConfig) -> list[str]:
    """Names are never reused, so deleting an observed obsolete name is safe.

    No callback/logout deletes a shared fixed-name cookie. Unknown fixed-name
    cookies from the old implementation are ignored and expire naturally.
    """
    hashes = _cookie_generations(request, config)
    live = {row.browser_hash for row in _generation_rows(db, config, hashes) if not row.revoked}
    return [generation_cookie(base, value) for value in sorted(hashes - live)
            for base in (config.transaction_cookie, config.session_cookie)
            if generation_cookie(base, value) in request.cookies][:MAX_COOKIE_DELETIONS]


def start_login(db: Session, config: OIDCConfig, request: Request) -> tuple[str, str, str]:
    # The first write serializes every following eligibility read on SQLite.
    cleanup(db)
    previous = browser_generations(db, request, config)
    current = get_session(db, request, config)
    preserve = current.session_hash if current else None
    superseded = [row.id for row in previous if not preserve or row.session_hash != preserve]
    pending = db.query(OIDCLoginTransaction).filter(~OIDCLoginTransaction.browser_hash.in_([row.browser_hash for row in previous])).count()
    if pending >= 128:
        db.commit()
        raise api_error(429, "oidc_busy", "登录请求过多，请稍后重试")
    _revoke(db, superseded)
    state, browser_binding, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
    browser_hash = digest(browser_binding)
    # Binding must outlive even a callback that finishes at the transaction TTL.
    generation = OIDCBrowserGeneration(browser_hash=browser_hash, config_binding=config.binding,
        expires_at=int(time.time()) + TRANSACTION_SECONDS + config.session_seconds)
    db.add(generation)
    db.add(OIDCLoginTransaction(state_hash=digest(state), browser_hash=browser_hash, nonce=nonce, verifier=verifier, config_binding=config.binding, expires_at=int(time.time()) + TRANSACTION_SECONDS))
    db.flush()
    generation_id = generation.id
    db.commit()
    # Reserve issuance order before provider latency can reorder concurrent
    # first-touch starts. Provider I/O never holds the SQLite write lock.
    try:
        with redact_http_logs(), http_client() as client:
            metadata = discover(config, client)
    except (OIDCError, httpx.HTTPError, ValueError, TypeError, KeyError):
        _revoke(db, [generation_id])
        db.commit()
        raise
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {"response_type": "code", "client_id": config.client_id, "redirect_uri": config.callback_url, "scope": "openid", "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256", "response_mode": "query"}
    return metadata["authorization_endpoint"] + "?" + urlencode(params), browser_binding, browser_hash


@dataclass(frozen=True)
class LoginTransaction:
    nonce: str
    verifier: str
    generation_id: int
    browser_hash: str
    observed_ids: tuple[int, ...]


def consume_transaction(db: Session, config: OIDCConfig, state: str, request: Request) -> LoginTransaction:
    if not state or len(state) > 256:
        raise OIDCError("Missing login transaction")
    filters = (OIDCLoginTransaction.state_hash == digest(state), OIDCLoginTransaction.config_binding == config.binding, OIDCLoginTransaction.expires_at > int(time.time()))
    transaction = db.query(OIDCLoginTransaction).filter(*filters).first()
    if transaction is None:
        raise OIDCError("Expired or invalid login transaction")
    binding = request.cookies.get(generation_cookie(config.transaction_cookie, transaction.browser_hash), "")
    if not binding or len(binding) > 256 or digest(binding) != transaction.browser_hash:
        raise OIDCError("Missing login binding")
    nonce, verifier, browser_hash = transaction.nonce, transaction.verifier, transaction.browser_hash
    # Authenticate state + binding before any mutation. Atomic delete claims this
    # callback and obtains SQLite's write lock before checking generation state.
    consumed = db.query(OIDCLoginTransaction).filter(*filters).delete(synchronize_session=False)
    generations = browser_generations(db, request, config)
    generation = next((row for row in generations if row.browser_hash == browser_hash), None)
    if consumed != 1 or generation is None or generation.revoked or generation.id != max(row.id for row in generations):
        if consumed == 1 and generation is not None:
            _revoke(db, [generation.id])
        db.commit()
        raise OIDCError("Superseded or already used login transaction")
    current = get_session(db, request, config)
    preserve = current.session_hash if current else None
    _revoke(db, [row.id for row in generations if row.id < generation.id and (not preserve or row.session_hash != preserve)])
    result = LoginTransaction(nonce, verifier, generation.id, browser_hash, tuple(row.id for row in generations))
    db.commit()
    return result


def cancel_transaction(db: Session, transaction: LoginTransaction) -> None:
    _revoke(db, [transaction.generation_id])
    db.commit()


def validate_id_token(config: OIDCConfig, metadata: dict, token: str, nonce: str, client: httpx.Client) -> dict:
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")
    if algorithm not in SIGNING_ALGORITHMS or algorithm not in metadata.get("id_token_signing_alg_values_supported", ["RS256"]):
        raise OIDCError("Unsupported ID token algorithm")
    jwks = _json_response(client.get(metadata["jwks_uri"]))
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise OIDCError("Invalid signing keys")
    candidates = [key for key in keys if isinstance(key, dict) and key.get("use", "sig") == "sig" and ("key_ops" not in key or "verify" in key["key_ops"]) and key.get("alg", algorithm) == algorithm and (not header.get("kid") or key.get("kid") == header["kid"]) and key.get("kty") in {"RSA", "EC", "OKP"}]
    if len(candidates) != 1:
        raise OIDCError("Missing or ambiguous ID token signing key")
    key = jwt.PyJWK.from_dict(candidates[0], algorithm=algorithm)
    claims = jwt.decode(token, key, algorithms=SIGNING_ALGORITHMS, audience=config.client_id, issuer=config.issuer, options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]}, leeway=30)
    audience = claims["aud"]
    if (isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != config.client_id) or ("azp" in claims and claims["azp"] != config.client_id):
        raise OIDCError("ID token authorized party mismatch")
    if not isinstance(claims["nonce"], str) or not secrets.compare_digest(claims["nonce"], nonce):
        raise OIDCError("ID token nonce mismatch")
    if not isinstance(claims["sub"], str) or claims["sub"] not in config.subjects:
        raise PermissionError("Identity is not authorized")
    return claims


def finish_login(db: Session, config: OIDCConfig, code: str, transaction: LoginTransaction) -> tuple[str, str]:
    if not code or len(code) > 8192:
        raise OIDCError("Missing authorization code")
    with redact_http_logs(), http_client() as client:
        metadata = discover(config, client)
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": config.callback_url, "code_verifier": transaction.verifier}
        auth = None
        if config.token_auth_method == "client_secret_basic":
            auth = httpx.BasicAuth(quote(config.client_id, safe=""), quote(config.client_secret, safe=""))
        else:
            data["client_id"] = config.client_id
            if config.token_auth_method == "client_secret_post":
                data["client_secret"] = config.client_secret
        token_response = _json_response(client.post(metadata["token_endpoint"], data=data, auth=auth))
        token = token_response.get("id_token")
        if not isinstance(token, str) or len(token) > 65536:
            raise OIDCError("Missing ID token")
        claims = validate_id_token(config, metadata, token, transaction.nonce, client)
    session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = int(time.time())
    # Claim under the write lock, after provider I/O. Logout/start can revoke a
    # consumed callback while it waits; both claim and insertion are one commit.
    claimed = db.query(OIDCBrowserGeneration).filter(
        OIDCBrowserGeneration.id == transaction.generation_id,
        OIDCBrowserGeneration.config_binding == config.binding,
        OIDCBrowserGeneration.revoked.is_(False),
        OIDCBrowserGeneration.session_hash.is_(None),
        OIDCBrowserGeneration.expires_at > now,
    ).update({"session_hash": digest(session_id)}, synchronize_session=False)
    if claimed != 1:
        db.rollback()
        raise OIDCError("Login generation was revoked")
    generation = db.query(OIDCBrowserGeneration).filter_by(id=transaction.generation_id).populate_existing().one()
    _revoke(db, [identity for identity in transaction.observed_ids if identity < generation.id])
    expires_at = min(now + config.session_seconds, generation.expires_at)
    generation.csrf_token, generation.subject, generation.session_expires_at = csrf, claims["sub"], expires_at
    db.add(OIDCSession(session_hash=digest(session_id), csrf_token=csrf, issuer=config.issuer, subject=claims["sub"], config_binding=config.binding, expires_at=expires_at))
    db.commit()
    return session_id, csrf


def get_session(db: Session, request: Request, config: OIDCConfig) -> OIDCSession | None:
    rows = browser_generations(db, request, config)
    for row in sorted(rows, key=lambda row: row.id, reverse=True):
        # A callback never rewrites the binding. A held credential response
        # cannot authenticate if that browser evidence was cleared or evicted.
        if row.revoked or not _has_binding_cookie(request, config, row) or not _has_session_cookie(request, config, row):
            continue
        session = db.query(OIDCSession).filter(OIDCSession.session_hash == row.session_hash,
            OIDCSession.expires_at > int(time.time()), OIDCSession.config_binding == config.binding).first()
        if session is not None and session.issuer == config.issuer and session.subject in config.subjects:
            return session
    return None


def logout_browser(db: Session, request: Request, config: OIDCConfig, *, password_authenticated: bool = False) -> None:
    # Serialize before loading the bounded logout proof and its observed scope.
    db.query(OIDCBrowserGeneration).filter(OIDCBrowserGeneration.id == -1).update({"revoked": True}, synchronize_session=False)
    rows = browser_generations(db, request, config)
    proofs = [row for row in rows if _has_session_cookie(request, config, row)
              and row.csrf_token and row.subject in config.subjects
              and row.session_expires_at and row.session_expires_at > int(time.time())]
    active_hashes = {value for (value,) in db.query(OIDCSession.session_hash).filter(
        OIDCSession.session_hash.in_([row.session_hash for row in rows if row.session_hash]),
        OIDCSession.expires_at > int(time.time()), OIDCSession.config_binding == config.binding,
    ).all()}
    cancellable = any(not row.revoked and (row.session_hash is None or row.session_hash in active_hashes) for row in rows)
    if not cancellable or (not proofs and not password_authenticated):
        db.rollback()
        return
    # A superseded session is proof only for logout, never authentication. This
    # closes commit-before-cookie-delivery without granting stale snapshots the
    # ability to revoke a later login whose binding they never carried.
    if password_authenticated:
        # The existing fallback Bearer is nonambient proof for cross-mode logout;
        # never extend it to SSO-only or requests without the canonical Origin.
        if request.headers.get("origin") != config.origin:
            raise api_error(403, "csrf_invalid", "请求验证失败，请刷新后重试")
    else:
        check_csrf(request, max(proofs, key=lambda row: row.id), config)
    _revoke(db, [row.id for row in rows])
    db.commit()


def check_csrf(request: Request, session: OIDCSession | OIDCBrowserGeneration, config: OIDCConfig) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    value = request.headers.get("X-CSRF-Token", "")
    if request.headers.get("origin") != config.origin or not value or not secrets.compare_digest(value, session.csrf_token):
        raise api_error(403, "csrf_invalid", "请求验证失败，请刷新后重试")


def session_is_active(identity: tuple[str, str]) -> bool:
    """Use a fresh DB read so live streams see logout committed elsewhere."""
    config = get_config()
    if config is None or identity[1] != config.binding:
        return False
    with SessionLocal() as db:
        session = db.query(OIDCSession).join(OIDCBrowserGeneration, OIDCBrowserGeneration.session_hash == OIDCSession.session_hash).filter(
            OIDCSession.session_hash == identity[0], OIDCSession.config_binding == config.binding,
            OIDCSession.expires_at > int(time.time()), OIDCBrowserGeneration.revoked.is_(False),
            OIDCBrowserGeneration.config_binding == config.binding, OIDCBrowserGeneration.expires_at > int(time.time())).first()
        return bool(session is not None and session.issuer == config.issuer and session.subject in config.subjects)
