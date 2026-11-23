"""Device pairing, session renewal and read-only mobile data."""

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from src.modules.mobile.alerts import create_alert_router
from src.modules.mobile.assistant import create_assistant_router, is_enabled as assistant_enabled
from src.modules.mobile.disclosures import create_disclosures_router
from src.modules.mobile.fact_snapshots import FactTarget, PortfolioContributions, Symbol, build_postmarket_snapshot
from src.modules.mobile.fact_tasks import create_fact_tasks_router
from src.modules.mobile.diagnostics import create_diagnostics_router
from src.modules.mobile.notifications import create_notifications_router
from src.modules.mobile.portfolio_history import PortfolioLookback, build_portfolio_lookback
from src.modules.mobile.portfolio_metrics import PortfolioMetrics, build_portfolio_metrics
from src.modules.mobile.portfolio_overview import PortfolioOverview, build_overview, build_portfolio
from src.modules.mobile.push import create_push_router, retire_device_push
from src.modules.mobile.symbol_facts import Market, create_symbol_facts_router
from src.modules.mobile.screening import create_screening_router
from src.modules.mobile.schemas import (
    Capabilities,
    DeviceGrant,
    DeviceSummary,
    SymbolHolding,
    MobileResponse,
    PairingClaim,
    PairingDecision,
    PairingQR,
    PairingStart,
    PairingStatus,
    PasswordLogin,
    Platform,
    Portfolio,
    WatchlistSymbol,
)
from src.modules.mobile.watchlist_overview import (
    PAGE_SIZE, WatchlistOverview, WatchlistTrend, load_watchlist_overview, load_watchlist_trends,
)
from src.platform.persistence.database import get_db
from src.platform.persistence.models import MobileDevice, MobileInstallation, MobilePairing, Stock
from src.web.errors import api_error

_PAIRING_TTL = 180
_DEVICE_TTL = 7 * 24 * 60 * 60
_ACCESS_TTL = 15 * 60
_REFRESH_TTL = 30 * 24 * 60 * 60
_REPLAY_TTL = 120
_PORTFOLIO_SCOPE = "portfolio:read"
_WATCHLIST_SCOPE = "watchlist:read"
_WATCHLIST_ADD_SCOPE = "watchlist:add"
_ALERTS_SCOPE = "alerts:manage"
_DIAGNOSTICS_SCOPE = "diagnostics:read"
_TOKEN_PREFIX = "pwmob_"
_REFRESH_PREFIX = "pwmor_"
_CONSOLE_PENDING = ("waiting_for_device", "waiting_for_approval", "approved")
_NO_STORE = {"Cache-Control": "no-store"}

DB = Annotated[Session, Depends(get_db)]
Bearer = Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))]


def _no_store(response: Response) -> None:
    response.headers.update(_NO_STORE)


phone = APIRouter()
owner = APIRouter()


class PasswordLoginRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def without_credentials_in_validation_errors(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise api_error(
                    422, "mobile_login_invalid", "请检查连接地址、账号和密码。", headers=_NO_STORE,
                ) from None

        return without_credentials_in_validation_errors


def _digest(material: str) -> str:
    return hashlib.sha256(material.encode()).hexdigest()


def _now() -> int:
    return int(time.time())


def _https_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme != "https" or not host or parsed.username is not None
            or parsed.path not in {"", "/"} or "?" in value or "#" in value
            or re.search(r"[\s\\]", value) or port == 0
        ):
            raise ValueError
        if ":" in host:
            host = f"[{ipaddress.IPv6Address(host)}]"
        else:
            host = host.encode("idna").decode("ascii")
            if not re.fullmatch(r"[a-z0-9.-]+", host):
                raise ValueError
    except (UnicodeError, ValueError):
        raise api_error(400, "mobile_https_required", "请使用有效的 HTTPS 服务地址。") from None
    return f"https://{host}" + (f":{port}" if port and port != 443 else "")


def _pairing_missing():
    return api_error(404, "mobile_pairing_not_found", "连接码无效或已失效。", headers=_NO_STORE)


def _pairing_closed():
    return api_error(409, "mobile_pairing_closed", "连接状态已变化，请重新扫码。", headers=_NO_STORE)


def _pairing_status(pairing: MobilePairing) -> PairingStatus:
    status = pairing.status
    if status in _CONSOLE_PENDING and pairing.expires_at <= _now():
        status = "expired"
    return PairingStatus(
        status=status,
        expires_at=pairing.expires_at,
        device_name=pairing.device_name,
        platform=pairing.platform,
        verification_code=pairing.verification_code,
        renewable=pairing.renewable,
        manage_alerts=pairing.manage_alerts,
        read_diagnostics=pairing.read_diagnostics,
        manage_watchlist=pairing.manage_watchlist,
    )


def _phone_claim_hash(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or not re.fullmatch(r"[0-9a-f]{64}", credentials.credentials):
        raise _pairing_missing()
    return _digest(credentials.credentials)


@owner.post("/pairings", status_code=201)
def start_pairing(body: PairingStart, request: Request, db: DB) -> MobileResponse[PairingQR]:
    origin = _https_origin(body.origin)
    browser_origin = request.headers.get("origin")
    if browser_origin is not None and origin != _https_origin(browser_origin):
        raise api_error(400, "mobile_origin_mismatch", "请从当前实例的设置页面生成连接码。")

    now = _now()
    challenge = secrets.token_urlsafe(32)
    pairing = MobilePairing(
        id=str(uuid4()),
        origin=origin,
        challenge_hash=_digest(challenge),
        status="waiting_for_device",
        expires_at=now + _PAIRING_TTL,
    )
    db.execute(delete(MobilePairing).where(MobilePairing.expires_at < now - 86_400))
    db.add(pairing)
    db.commit()
    return MobileResponse(data=PairingQR(
        pairing_id=pairing.id, origin=origin, challenge=challenge, expires_at=pairing.expires_at,
    ))


@owner.get("/pairings/{pairing_id}")
def read_web_pairing(pairing_id: UUID, db: DB) -> MobileResponse[PairingStatus]:
    pairing = db.get(MobilePairing, str(pairing_id))
    if pairing is None:
        raise _pairing_missing()
    return MobileResponse(data=_pairing_status(pairing))


@phone.post("/pairings/{pairing_id}/claim")
def claim_pairing(pairing_id: UUID, body: PairingClaim, db: DB) -> MobileResponse[PairingStatus]:
    key = str(pairing_id)
    challenge_hash = _digest(body.challenge)
    claim_hash = _digest(body.claim_key)
    db.execute(
        update(MobilePairing).where(
            MobilePairing.id == key,
            MobilePairing.status == "waiting_for_device",
            MobilePairing.expires_at > _now(),
            MobilePairing.challenge_hash == challenge_hash,
        ).values(
            status="waiting_for_approval",
            claim_hash=claim_hash,
            verification_code=f"{secrets.randbelow(1_000_000):06d}",
            device_name=body.device_name,
            platform=body.platform,
            renewable=body.renewable,
            manage_alerts=body.renewable and body.manage_alerts,
            read_diagnostics=body.renewable and body.read_diagnostics,
            manage_watchlist=body.renewable and body.manage_watchlist,
        )
    )
    pairing = db.get(MobilePairing, key)
    if pairing is None or not secrets.compare_digest(pairing.challenge_hash, challenge_hash):
        raise _pairing_missing()
    result = _pairing_status(pairing)
    if result.status not in {"waiting_for_approval", "approved"} or pairing.claim_hash != claim_hash:
        raise _pairing_closed()
    db.commit()
    return MobileResponse(data=result)


@phone.get("/pairings/{pairing_id}/status")
def read_phone_pairing(pairing_id: UUID, credentials: Bearer, db: DB) -> MobileResponse[PairingStatus]:
    claim_hash = _phone_claim_hash(credentials)
    pairing = db.get(MobilePairing, str(pairing_id))
    if pairing is None or not secrets.compare_digest(pairing.claim_hash or "", claim_hash):
        raise _pairing_missing()
    return MobileResponse(data=_pairing_status(pairing))


@owner.post("/pairings/{pairing_id}/decision")
def decide_pairing(pairing_id: UUID, body: PairingDecision, db: DB) -> MobileResponse[PairingStatus]:
    key = str(pairing_id)
    query = update(MobilePairing).where(
        MobilePairing.id == key, MobilePairing.expires_at > _now(),
    )
    if body.decision == "cancel":
        query = query.where(MobilePairing.status.in_(_CONSOLE_PENDING)).values(status="cancelled")
    else:
        if body.verification_code is None:
            raise api_error(400, "mobile_verification_required", "请核对手机上的校验码。")
        query = query.where(
            MobilePairing.status == "waiting_for_approval",
            MobilePairing.verification_code == body.verification_code,
        ).values(status="approved" if body.decision == "approve" else "denied")
    if db.execute(query).rowcount != 1:
        raise _pairing_closed()
    pairing = db.get(MobilePairing, key)
    result = _pairing_status(pairing)
    db.commit()
    return MobileResponse(data=result)


def _device_grant(db: Session, device: MobileDevice, token: str, refresh_token: str | None) -> DeviceGrant:
    installation = db.get(MobileInstallation, 1)
    scopes = device.scope.split()
    return DeviceGrant(
        instance_id=installation.instance_id, device_id=device.id, origin=device.origin,
        access_token=token, expires_at=device.access_expires_at or device.expires_at,
        refresh_token=refresh_token, refresh_expires_at=device.expires_at if refresh_token else None,
        watchlist_read=_WATCHLIST_SCOPE in scopes,
        watchlist_overview=_WATCHLIST_SCOPE in scopes,
        assistant_chat=_WATCHLIST_SCOPE in scopes and _PORTFOLIO_SCOPE in scopes and assistant_enabled(db),
        alerts_manage=_ALERTS_SCOPE in scopes,
        diagnostics_read=_DIAGNOSTICS_SCOPE in scopes,
        watchlist_manage=_WATCHLIST_ADD_SCOPE in scopes,
    )


def _issue_device(
    db: Session, name: str, platform: Platform, origin: str, now: int, *, renewable: bool,
    manage_alerts: bool = False, read_diagnostics: bool = False, manage_watchlist: bool = False,
) -> DeviceGrant:
    token = f"{_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    refresh_token = f"{_REFRESH_PREFIX}{secrets.token_urlsafe(32)}" if renewable else None
    scopes = [_PORTFOLIO_SCOPE]
    if renewable:
        scopes.append(_WATCHLIST_SCOPE)
        if manage_alerts:
            scopes.append(_ALERTS_SCOPE)
        if read_diagnostics:
            scopes.append(_DIAGNOSTICS_SCOPE)
        if manage_watchlist:
            scopes.append(_WATCHLIST_ADD_SCOPE)
    device = MobileDevice(
        id=str(uuid4()), name=name, platform=platform, origin=origin,
        token_hash=_digest(token), scope=" ".join(scopes),
        created_at=now, expires_at=now + (_REFRESH_TTL if renewable else _DEVICE_TTL),
        access_expires_at=now + _ACCESS_TTL if renewable else None,
        refresh_token_hash=_digest(refresh_token) if refresh_token else None,
    )
    db.add(device)
    db.execute(
        insert(MobileInstallation).values(id=1, instance_id=str(uuid4()))
        .on_conflict_do_nothing(index_elements=["id"])
    )
    return _device_grant(db, device, token, refresh_token)


@phone.post("/pairings/{pairing_id}/exchange")
def exchange_pairing(pairing_id: UUID, credentials: Bearer, db: DB) -> MobileResponse[DeviceGrant]:
    claim_hash = _phone_claim_hash(credentials)
    key = str(pairing_id)
    now = _now()
    # The update and the device insert commit together, including concurrent exchanges.
    redeemed = db.execute(
        update(MobilePairing).where(
            MobilePairing.id == key,
            MobilePairing.status == "approved",
            MobilePairing.claim_hash == claim_hash,
            MobilePairing.expires_at > now,
        ).values(status="redeemed")
    )
    if redeemed.rowcount != 1:
        raise _pairing_closed()
    pairing = db.get(MobilePairing, key)
    result = _issue_device(
        db, pairing.device_name, pairing.platform, pairing.origin, now,
        renewable=pairing.renewable, manage_alerts=pairing.manage_alerts, read_diagnostics=pairing.read_diagnostics,
        manage_watchlist=pairing.manage_watchlist,
    )
    db.commit()
    return MobileResponse(data=result)


@owner.get("/devices")
def list_devices(db: DB) -> MobileResponse[list[DeviceSummary]]:
    devices = db.scalars(select(MobileDevice).order_by(MobileDevice.created_at.desc())).all()
    return MobileResponse(data=[DeviceSummary.model_validate(device, from_attributes=True) for device in devices])


def _revoke_device(db: Session, device_id: str, now: int) -> None:
    db.execute(
        update(MobileDevice).where(MobileDevice.id == device_id, MobileDevice.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    retire_device_push(db, device_id, now)


@owner.delete("/devices/{device_id}")
def revoke_device(device_id: UUID, db: DB) -> MobileResponse[DeviceSummary]:
    key = str(device_id)
    _revoke_device(db, key, _now())
    device = db.get(MobileDevice, key)
    if device is None:
        raise api_error(404, "mobile_device_not_found", "未找到此设备。")
    result = DeviceSummary.model_validate(device, from_attributes=True)
    db.commit()
    return MobileResponse(data=result)


def _invalid_device():
    return api_error(401, "mobile_device_invalid", "请重新连接手机。", headers=_NO_STORE)


def _expired_device():
    return api_error(401, "mobile_device_expired", "设备授权已过期，请重新连接手机。", headers=_NO_STORE)


def _require_active_device(device: MobileDevice | None, now: int) -> MobileDevice:
    if device is None:
        raise _invalid_device()
    if device.revoked_at is not None:
        raise api_error(401, "mobile_device_revoked", "设备授权已撤销，请重新连接手机。", headers=_NO_STORE)
    if device.expires_at <= now:
        raise _expired_device()
    return device


def _verify_device(credentials: HTTPAuthorizationCredentials | None, db: Session, *scopes: str) -> MobileDevice:
    token = credentials.credentials if credentials else ""
    if not re.fullmatch(r"pwmob_[A-Za-z0-9_-]{43}", token):
        raise _invalid_device()
    now = _now()
    device = _require_active_device(db.scalar(select(MobileDevice).where(MobileDevice.token_hash == _digest(token))), now)
    if device.access_expires_at is not None and device.access_expires_at <= now:
        raise _expired_device()
    if not set(scopes).issubset(device.scope.split()):
        raise api_error(403, "mobile_scope_required", "请重新连接以授权此功能。", headers=_NO_STORE)
    return device


@phone.delete("/session")
def discard_device_session(credentials: Bearer, db: DB) -> MobileResponse[bool]:
    device = _verify_device(credentials, db, _PORTFOLIO_SCOPE)
    _revoke_device(db, device.id, _now())
    db.commit()
    return MobileResponse(data=True)


def _rotated_token(old_token: str, request_key: str, prefix: str) -> str:
    # A retry can recompute the issued secrets without retaining plaintext in the database.
    digest = hmac.digest(old_token.encode(), f"panwatch/mobile/v1/{prefix}/{request_key}".encode(), "sha256")
    return prefix + base64.urlsafe_b64encode(digest).decode().rstrip("=")


@phone.post("/session/refresh")
def refresh_session(
    credentials: Bearer, db: DB,
    request_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> MobileResponse[DeviceGrant]:
    current_token = credentials.credentials if credentials else ""
    if not re.fullmatch(r"pwmor_[A-Za-z0-9_-]{43}", current_token):
        raise _invalid_device()
    if not request_key or not re.fullmatch(r"[0-9a-f]{64}", request_key):
        raise api_error(400, "mobile_refresh_key_invalid", "缺少有效的设备续期请求标识。", headers=_NO_STORE)

    now = _now()
    current_hash = _digest(current_token)
    request_hash = _digest(request_key)
    token = _rotated_token(current_token, request_key, _TOKEN_PREFIX)
    refresh_token = _rotated_token(current_token, request_key, _REFRESH_PREFIX)
    token_hash = _digest(token)
    refresh_hash = _digest(refresh_token)
    device = db.scalar(
        update(MobileDevice).where(
            MobileDevice.refresh_token_hash == current_hash,
            MobileDevice.revoked_at.is_(None),
            MobileDevice.expires_at > now,
        ).values(
            token_hash=token_hash, refresh_token_hash=refresh_hash,
            access_expires_at=now + _ACCESS_TTL, expires_at=now + _REFRESH_TTL,
            previous_refresh_hash=current_hash, refresh_request_hash=request_hash,
            refresh_replay_until=now + _REPLAY_TTL,
        ).returning(MobileDevice)
    )
    if device is None:
        device = _require_active_device(db.scalar(select(MobileDevice).where(or_(
            MobileDevice.refresh_token_hash == current_hash,
            MobileDevice.previous_refresh_hash == current_hash,
        ))), now)
        if (
            device.previous_refresh_hash != current_hash
            or device.refresh_request_hash != request_hash
            or (device.refresh_replay_until or 0) <= now
            or device.token_hash != token_hash
            or device.refresh_token_hash != refresh_hash
        ):
            raise _invalid_device()
    result = _device_grant(db, device, token, refresh_token)
    db.commit()
    return MobileResponse(data=result)


def create_router(
    *,
    get_current_user: Callable[..., str | None],
    get_password_hash: Callable[[Session], str | None],
    get_stored_username: Callable[[Session], str | None],
    verify_login_credentials: Callable[[Session, str, str], None],
    get_app_version: Callable[[], str],
    get_portfolio_summary: Callable[[Session], dict],
    load_positions_without_quotes: Callable[[Session, int | None], dict],
    read_agent_health: Callable[[Session], dict],
    aggregate_klines: Callable[[list, str], list],
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(_no_store)])
    password = APIRouter(route_class=PasswordLoginRoute)

    def require_owner(user: Annotated[str | None, Depends(get_current_user)]) -> None:
        if user is None:
            raise api_error(403, "mobile_login_setup_required", "请先设置 PanWatch 登录密码。")

    @router.get("/capabilities")
    def capabilities(db: DB) -> MobileResponse[Capabilities]:
        pairing = bool(get_password_hash(db))
        return MobileResponse(data=Capabilities(
            service_version=get_app_version(), pairing=pairing, assistant_chat=pairing and assistant_enabled(db),
            password_login=pairing and bool(get_stored_username(db)),
        ))

    @password.post("/devices/login", status_code=201)
    def login_device(body: PasswordLogin, db: DB) -> MobileResponse[DeviceGrant]:
        origin = _https_origin(body.origin)
        verify_login_credentials(db, body.username, body.password.get_secret_value())
        grant = _issue_device(
            db, body.device_name, body.platform, origin, _now(), renewable=body.renewable,
            manage_alerts=body.manage_alerts, read_diagnostics=body.read_diagnostics, manage_watchlist=body.manage_watchlist,
        )
        db.commit()
        return MobileResponse(data=grant)

    @router.get("/portfolio")
    def portfolio(credentials: Bearer, db: DB) -> MobileResponse[Portfolio]:
        _verify_device(credentials, db, _PORTFOLIO_SCOPE)
        return MobileResponse(data=build_portfolio(get_portfolio_summary(db)))

    @router.get("/portfolio/overview")
    def portfolio_overview(credentials: Bearer, db: DB) -> MobileResponse[PortfolioOverview]:
        _verify_device(credentials, db, _PORTFOLIO_SCOPE)
        return MobileResponse(data=build_overview(get_portfolio_summary(db)))

    @router.get("/portfolio/positions")
    def symbol_positions(
        credentials: Bearer, db: DB, market: Literal["CN", "HK", "US"],
        symbol: Annotated[str, Query(min_length=1, max_length=80)],
    ) -> MobileResponse[list[SymbolHolding]]:
        _verify_device(credentials, db, _PORTFOLIO_SCOPE)
        summary = get_portfolio_summary(db)
        return MobileResponse(data=[
            SymbolHolding(
                symbol=symbol,
                name=position["name"],
                market=market,
                quantity=position["quantity"],
                market_value=position["market_value"],
                currency={"CN": "CNY", "HK": "HKD", "US": "USD"}[market],
                position_id=position["id"],
                account_id=account["id"],
                account_name=account["name"],
                cost_price=position["cost_price"],
                daily_pnl_cny=position["daily_pnl"],
                # The shared summary omits a zero P/L, but an available valuation makes it known.
                unrealized_pnl_cny=(position["pnl"] or 0.0) if position["market_value_cny"] is not None else None,
            )
            for account in summary["accounts"] for position in account["positions"]
            if position["market"] == market and position["symbol"] == symbol
        ])

    @router.get("/portfolio/metrics")
    def portfolio_metrics(credentials: Bearer, db: DB) -> MobileResponse[PortfolioMetrics]:
        _verify_device(credentials, db, _PORTFOLIO_SCOPE)
        return MobileResponse(data=build_portfolio_metrics(get_portfolio_summary(db)))

    @router.get("/portfolio/contributions")
    def portfolio_contributions(
        credentials: Bearer, db: DB,
        market: Market | None = None, symbol: Annotated[Symbol | None, Query()] = None,
        account_id: Annotated[int | None, Query(ge=1)] = None,
    ) -> MobileResponse[PortfolioContributions]:
        _verify_device(credentials, db, _PORTFOLIO_SCOPE)
        if (market is None) != (symbol is None):
            raise api_error(400, "mobile_contribution_target_incomplete", "请同时提供市场和标的代码。")
        target = FactTarget(market=market, symbol=symbol) if market is not None and symbol is not None else None
        summary = get_portfolio_summary(db)
        if account_id is not None:
            account = next((row for row in summary["accounts"] if row["id"] == account_id), None)
            if account is None:
                raise api_error(404, "mobile_portfolio_account_not_found", "此账户不可用。")
            summary = {"accounts": [account], "total": account}
        snapshot = build_postmarket_snapshot(summary, target)
        return MobileResponse(data=PortfolioContributions(
            **snapshot.model_dump(), computed_at=datetime.now(UTC), account_id=account_id,
        ))

    @router.get("/portfolio/metrics/history")
    def portfolio_history(
        credentials: Bearer, db: DB, account_id: Annotated[int | None, Query(ge=1)] = None,
    ) -> MobileResponse[PortfolioLookback]:
        _verify_device(credentials, db, _PORTFOLIO_SCOPE)
        summary = load_positions_without_quotes(db, account_id)
        if account_id is not None and not summary["accounts"]:
            raise api_error(404, "mobile_portfolio_account_not_found", "此账户不可用。")
        return MobileResponse(data=build_portfolio_lookback(summary))

    @router.get("/symbols/watchlist")
    def watchlist(credentials: Bearer, db: DB) -> MobileResponse[list[WatchlistSymbol]]:
        _verify_device(credentials, db, _WATCHLIST_SCOPE)
        rows = db.execute(select(Stock.symbol, Stock.name, Stock.market).order_by(Stock.sort_order, Stock.id))
        return MobileResponse(data=[WatchlistSymbol(symbol=symbol, name=name, market=market) for symbol, name, market in rows])

    @router.get("/symbols/watchlist/overview")
    def watchlist_overview(
        credentials: Bearer, db: DB, offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=PAGE_SIZE)] = PAGE_SIZE,
    ) -> MobileResponse[WatchlistOverview]:
        device = _verify_device(credentials, db, _WATCHLIST_SCOPE)
        return MobileResponse(data=load_watchlist_overview(
            db, offset=offset, limit=limit, read_alerts=_ALERTS_SCOPE in device.scope.split(),
        ))

    @router.get("/symbols/watchlist/trends")
    def watchlist_trends(
        credentials: Bearer, db: DB, offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=PAGE_SIZE)] = PAGE_SIZE,
    ) -> MobileResponse[list[WatchlistTrend]]:
        _verify_device(credentials, db, _WATCHLIST_SCOPE)
        return MobileResponse(data=load_watchlist_trends(db, offset=offset, limit=limit))

    def require_alert_device(credentials: Bearer, db: DB) -> MobileDevice:
        return _verify_device(credentials, db, _ALERTS_SCOPE)

    def require_diagnostics_device(credentials: Bearer, db: DB) -> MobileDevice:
        return _verify_device(credentials, db, _DIAGNOSTICS_SCOPE)

    def require_symbol_device(credentials: Bearer, db: DB) -> MobileDevice:
        return _verify_device(credentials, db, _WATCHLIST_SCOPE)

    def require_fact_task_device(credentials: Bearer, db: DB) -> MobileDevice:
        return _verify_device(credentials, db, _WATCHLIST_SCOPE, _PORTFOLIO_SCOPE)

    def require_watchlist_add(credentials: Bearer, db: DB) -> MobileDevice:
        return _verify_device(credentials, db, _WATCHLIST_ADD_SCOPE)

    router.include_router(create_screening_router(require_symbol_device, require_watchlist_add))
    router.include_router(create_symbol_facts_router(require_symbol_device, aggregate_klines))
    router.include_router(create_disclosures_router(require_symbol_device))
    router.include_router(create_fact_tasks_router(require_fact_task_device, get_portfolio_summary))

    def require_assistant_device(credentials: Bearer, db: DB) -> MobileDevice:
        device = require_fact_task_device(credentials, db)
        if not assistant_enabled(db):
            raise api_error(503, "mobile_assistant_unavailable", "此实例尚未启用移动事实对话。", headers=_NO_STORE)
        return device

    router.include_router(create_assistant_router(require_assistant_device, get_portfolio_summary, aggregate_klines))
    router.include_router(create_notifications_router(require_fact_task_device, _ALERTS_SCOPE))
    router.include_router(create_diagnostics_router(require_diagnostics_device, read_agent_health))
    router.include_router(phone)
    router.include_router(create_alert_router(require_alert_device))
    router.include_router(create_push_router(require_alert_device))
    router.include_router(owner, dependencies=[Depends(require_owner)])
    router.include_router(password)
    return router
