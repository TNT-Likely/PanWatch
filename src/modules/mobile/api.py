"""I0 device pairing and the read-only mobile portfolio."""

import hashlib
import ipaddress
import re
import secrets
import time
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from src.modules.administration.api.auth import (
    get_current_user, get_password_hash, get_stored_username, security, verify_login_credentials,
)
from src.modules.administration.api.settings import get_app_version
from src.modules.mobile.schemas import (
    Capabilities,
    DeviceGrant,
    DeviceSummary,
    Holding,
    MobileResponse,
    PairingClaim,
    PairingDecision,
    PairingQR,
    PairingStart,
    PairingStatus,
    PasswordLogin,
    Platform,
    Portfolio,
)
from src.modules.portfolio.api.accounts import get_portfolio_summary
from src.platform.persistence.database import get_db
from src.platform.persistence.models import MobileDevice, MobileInstallation, MobilePairing
from src.web.errors import api_error

_PAIRING_TTL = 180
_DEVICE_TTL = 7 * 24 * 60 * 60
_PORTFOLIO_SCOPE = "portfolio:read"
_TOKEN_PREFIX = "pwmob_"
_CONSOLE_PENDING = ("waiting_for_device", "waiting_for_approval", "approved")
_NO_STORE = {"Cache-Control": "no-store"}

DB = Annotated[Session, Depends(get_db)]
Bearer = Annotated[HTTPAuthorizationCredentials | None, Depends(security)]


def _no_store(response: Response) -> None:
    response.headers.update(_NO_STORE)


def _require_owner(user: Annotated[str | None, Depends(get_current_user)]) -> None:
    if user is None:
        raise api_error(403, "mobile_login_setup_required", "请先设置 PanWatch 登录密码。")


router = APIRouter(dependencies=[Depends(_no_store)])
owner = APIRouter(dependencies=[Depends(_require_owner)])


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


password = APIRouter(route_class=PasswordLoginRoute)


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
    )


def _phone_claim_hash(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or not re.fullmatch(r"[0-9a-f]{64}", credentials.credentials):
        raise _pairing_missing()
    return _digest(credentials.credentials)


@router.get("/capabilities")
def capabilities(db: DB) -> MobileResponse[Capabilities]:
    pairing = bool(get_password_hash(db))
    return MobileResponse(data=Capabilities(
        service_version=get_app_version(), pairing=pairing,
        password_login=pairing and bool(get_stored_username(db)),
    ))


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


@router.post("/pairings/{pairing_id}/claim")
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


@router.get("/pairings/{pairing_id}/status")
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


def _issue_device(db: Session, name: str, platform: Platform, origin: str, now: int) -> DeviceGrant:
    token = f"{_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    device_id = str(uuid4())
    expires_at = now + _DEVICE_TTL
    db.add(MobileDevice(
        id=device_id, name=name, platform=platform,
        token_hash=_digest(token), scope=_PORTFOLIO_SCOPE,
        created_at=now, expires_at=expires_at,
    ))
    db.execute(
        insert(MobileInstallation).values(id=1, instance_id=str(uuid4()))
        .on_conflict_do_nothing(index_elements=["id"])
    )
    installation = db.get(MobileInstallation, 1)
    return DeviceGrant(
        instance_id=installation.instance_id, device_id=device_id, origin=origin,
        access_token=token, expires_at=expires_at,
    )


@password.post("/devices/login", status_code=201)
def login_device(body: PasswordLogin, db: DB) -> MobileResponse[DeviceGrant]:
    origin = _https_origin(body.origin)
    verify_login_credentials(db, body.username, body.password.get_secret_value())
    grant = _issue_device(db, body.device_name, body.platform, origin, _now())
    db.commit()
    return MobileResponse(data=grant)


@router.post("/pairings/{pairing_id}/exchange")
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
    result = _issue_device(db, pairing.device_name, pairing.platform, pairing.origin, now)
    db.commit()
    return MobileResponse(data=result)


@owner.get("/devices")
def list_devices(db: DB) -> MobileResponse[list[DeviceSummary]]:
    devices = db.scalars(select(MobileDevice).order_by(MobileDevice.created_at.desc())).all()
    return MobileResponse(data=[DeviceSummary.model_validate(device, from_attributes=True) for device in devices])


@owner.delete("/devices/{device_id}")
def revoke_device(device_id: UUID, db: DB) -> MobileResponse[DeviceSummary]:
    key = str(device_id)
    db.execute(
        update(MobileDevice).where(MobileDevice.id == key, MobileDevice.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    device = db.get(MobileDevice, key)
    if device is None:
        raise api_error(404, "mobile_device_not_found", "未找到此设备。")
    result = DeviceSummary.model_validate(device, from_attributes=True)
    db.commit()
    return MobileResponse(data=result)


def _verify_device(credentials: HTTPAuthorizationCredentials | None, db: Session) -> None:
    token = credentials.credentials if credentials else ""
    if not re.fullmatch(r"pwmob_[A-Za-z0-9_-]{43}", token):
        raise api_error(401, "mobile_device_invalid", "请重新连接手机。")
    device = db.scalar(select(MobileDevice).where(MobileDevice.token_hash == _digest(token)))
    if device is None or device.scope != _PORTFOLIO_SCOPE:
        raise api_error(401, "mobile_device_invalid", "请重新连接手机。")
    if device.revoked_at is not None:
        raise api_error(401, "mobile_device_revoked", "设备授权已撤销，请重新扫码。")
    if device.expires_at <= _now():
        raise api_error(401, "mobile_device_expired", "设备授权已过期，请重新扫码。")


@router.get("/portfolio")
def portfolio(credentials: Bearer, db: DB) -> MobileResponse[Portfolio]:
    _verify_device(credentials, db)
    summary = get_portfolio_summary(db=db)
    holdings = [
        Holding(
            symbol=position["symbol"],
            name=position["name"],
            market=position["market"],
            quantity=position["quantity"],
            market_value=position["market_value"],
            currency={"HK": "HKD", "US": "USD"}.get(position["market"], "CNY"),
        )
        for account in summary["accounts"] for position in account["positions"]
    ]
    covered = sum(holding.market_value is not None for holding in holdings)
    valuation = "complete"
    if covered < len(holdings):
        valuation = "partial" if covered else "unavailable"
    return MobileResponse(data=Portfolio(
        total_assets=summary["total"]["total_assets"], valuation=valuation, holdings=holdings,
    ))


router.include_router(owner)
router.include_router(password)
