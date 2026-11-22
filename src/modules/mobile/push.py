"""Register a device's opaque route and relay generic notifications."""

import os
import time
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, SecretStr, StringConstraints
from sqlalchemy import literal, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, aliased

from src.modules.mobile.schemas import MobileResponse
from src.platform.persistence.database import get_db
from src.platform.persistence.models import MobileDevice, MobileInstallation, MobilePushRoute, PriceAlertDelivery, PriceAlertHit
from src.web.errors import api_error

DB = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True, repr=False)
class Gateway:
    origin: str
    enrollment_key: str

    def request(self, method: str, path: str, bearer: str, body: dict) -> dict:
        response = httpx.request(
            method, self.origin + path, json=body, headers={"Authorization": f"Bearer {bearer}"},
            timeout=8, follow_redirects=False,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("invalid gateway response")
        return result


def configured_gateway() -> Gateway | None:
    url = os.getenv("PANWATCH_MOBILE_PUSH_GATEWAY_URL", "")
    key = os.getenv("PANWATCH_MOBILE_PUSH_ENROLLMENT_KEY", "")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme == "https" and parsed.hostname and parsed.path in ("", "/")
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
            and not any(char.isspace() for char in parsed.netloc)
            and (parsed.port is None or parsed.port > 0)
            and len(key) >= 32 and not any(char.isspace() for char in key)
        ):
            return Gateway(f"https://{parsed.netloc}", key)
    except ValueError:
        pass
    return None


class PushRegistration(BaseModel):
    token: SecretStr
    environment: Literal["sandbox", "production"]


class PushRouteState(BaseModel):
    available: bool
    route_id: UUID | None = None
    test_available: bool = False


class PushTestState(BaseModel):
    state: Literal["queued", "accepted"]


class GatewayRoute(BaseModel):
    route_id: UUID
    submit_token: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]


def _active_route(device_id: str):
    return select(MobilePushRoute).where(
        MobilePushRoute.device_id == device_id, MobilePushRoute.retired_at.is_(None),
    )


async def _empty_test_request(request: Request) -> None:
    if not request.query_params:
        async for chunk in request.stream():
            if chunk:
                break
        else:
            return
    raise api_error(400, "mobile_push_test_parameters_invalid", "测试通知不接受正文或目标参数。")


def _reserve_test_route(db: Session, device_id: str, now: int):
    active = db.scalar(_active_route(device_id))
    if active is None or not active.gateway_route_id or not active.submit_token:
        raise api_error(409, "mobile_push_route_required", "请先为此设备开启通知。")
    active_id = active.id
    minute = now // 60
    previous = aliased(MobilePushRoute)
    # Retired routes retain the reservation so re-enrollment cannot bypass the device limit.
    another_route_used_minute = select(previous.id).where(
        previous.device_id == device_id, previous.id != active_id, previous.test_minute >= minute,
    ).exists()
    device_still_active = select(MobileDevice.id).where(
        MobileDevice.id == device_id, MobileDevice.revoked_at.is_(None), MobileDevice.expires_at > now,
        or_(MobileDevice.access_expires_at.is_(None), MobileDevice.access_expires_at > now),
    ).exists()
    route = db.execute(update(MobilePushRoute).where(
        MobilePushRoute.id == active_id, MobilePushRoute.retired_at.is_(None),
        MobilePushRoute.gateway_route_id.is_not(None), MobilePushRoute.submit_token.is_not(None),
        or_(MobilePushRoute.test_minute.is_(None), MobilePushRoute.test_minute <= minute),
        ~another_route_used_minute, device_still_active,
    ).values(test_minute=minute).returning(
        MobilePushRoute.instance_id, MobilePushRoute.gateway_route_id, MobilePushRoute.submit_token,
    )).one_or_none()
    db.commit()
    if route is None:
        if not db.scalar(select(device_still_active)):
            raise api_error(401, "mobile_device_invalid", "设备授权已失效，请重新连接。")
        current_id = db.scalar(_active_route(device_id).with_only_columns(MobilePushRoute.id))
        if current_id != active_id:
            raise api_error(409, "mobile_push_route_required", "请重新为此设备开启通知。")
        raise api_error(
            429, "mobile_push_test_rate_limited", "每台设备每分钟可发送一次测试通知。",
            headers={"Retry-After": str(60 - now % 60)},
        )
    return route


def _retire(db: Session, predicate, now: int):
    db.execute(update(MobilePushRoute).where(
        predicate, MobilePushRoute.retired_at.is_(None),
    ).values(retired_at=now, withdraw_after=now, submit_token=None))


def retire_device_push(db: Session, device_id: str, now: int):
    _retire(db, MobilePushRoute.device_id == device_id, now)


def withdraw_pending(session_factory, limit: int = 3):
    gateway = configured_gateway()
    if gateway is None:
        return
    now = int(time.time())
    with session_factory() as db:
        _retire(db, MobilePushRoute.device_id.in_(select(MobileDevice.id).where(
            MobileDevice.expires_at <= now,
        )), now)
        due = db.execute(select(
            MobilePushRoute.id, MobilePushRoute.instance_id, MobilePushRoute.withdraw_attempts,
        ).where(MobilePushRoute.withdraw_after <= now).order_by(MobilePushRoute.withdraw_after).limit(limit)).all()
        db.commit()
    for route_id, instance_id, attempts in due:
        next_check = now + min(3600, 60 * 2 ** min(attempts, 6))
        with session_factory() as db:
            claimed = db.execute(update(MobilePushRoute).where(
                MobilePushRoute.id == route_id, MobilePushRoute.withdraw_after <= now,
            ).values(withdraw_after=next_check, withdraw_attempts=attempts + 1)).rowcount
            db.commit()
        if not claimed:
            continue
        try:
            reply = gateway.request("POST", "/v1/enrollments/revoke", gateway.enrollment_key, {
                "instance_id": instance_id, "device_id": route_id,
            })
        except (httpx.HTTPError, ValueError):
            continue
        if reply.get("state") == "revoked":
            with session_factory() as db:
                db.execute(update(MobilePushRoute).where(
                    MobilePushRoute.id == route_id,
                ).values(withdraw_after=None))
                db.commit()


def relay(event_id: str, route_id: str, submit_token: str) -> str:
    gateway = configured_gateway()
    if gateway is None:
        return "channel_unavailable"
    try:
        data = gateway.request("POST", "/v1/deliveries", submit_token, {
            "route_id": route_id, "event_id": event_id,
        })
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403, 410):
            return "channel_unavailable"
        return "mobile_push_failed"
    except (httpx.HTTPError, ValueError):
        return "mobile_push_failed"
    state = data.get("state")
    if state == "accepted":
        return ""
    if state in ("queued", "leased"):
        return "mobile_push_pending"
    return "mobile_push_failed"


def mobile_routes(db: Session) -> list[MobilePushRoute]:
    if configured_gateway() is None:
        return []
    return list(db.scalars(select(MobilePushRoute).join(
        MobileDevice, MobileDevice.id == MobilePushRoute.device_id,
    ).where(
        MobileDevice.revoked_at.is_(None), MobileDevice.expires_at > int(time.time()),
        MobilePushRoute.retired_at.is_(None), MobilePushRoute.submit_token.is_not(None),
    )))


def create_push_router(require_device) -> APIRouter:
    router = APIRouter()
    Device = Annotated[MobileDevice, Depends(require_device)]

    @router.get("/push")
    def status(db: DB, device: Device) -> MobileResponse[PushRouteState]:
        route = db.scalar(_active_route(device.id))
        gateway = configured_gateway()
        return MobileResponse(data=PushRouteState(
            available=gateway is not None,
            route_id=route.gateway_route_id if route else None,
            test_available=gateway is not None and bool(route and route.gateway_route_id and route.submit_token),
        ))

    @router.put("/push")
    def register(body: PushRegistration, db: DB, device: Device) -> MobileResponse[PushRouteState]:
        gateway = configured_gateway()
        if gateway is None:
            raise api_error(503, "mobile_push_unavailable", "此服务尚未启用推送。")
        token = body.token.get_secret_value()
        if (
            not 1 <= len(token) <= 4096 or any(char.isspace() for char in token)
            or (device.platform == "android" and body.environment != "production")
        ):
            raise api_error(400, "mobile_push_token_invalid", "设备推送凭据无效。")
        now = int(time.time())
        device_id, platform = device.id, device.platform
        instance_id = db.get(MobileInstallation, 1).instance_id
        active_device = select(literal(str(uuid4())), MobileDevice.id, literal(instance_id), literal(0)).where(
            MobileDevice.id == device_id, MobileDevice.revoked_at.is_(None), MobileDevice.expires_at > now,
        )
        db.execute(insert(MobilePushRoute).from_select(
            ["id", "device_id", "instance_id", "withdraw_attempts"], active_device,
        ).on_conflict_do_nothing(index_elements=["device_id"], index_where=MobilePushRoute.retired_at.is_(None)))
        route = db.scalar(_active_route(device_id))
        if route is None:
            raise api_error(401, "mobile_device_revoked", "设备授权已失效，请重新连接。")
        local_id = route.id
        db.commit()
        try:
            reply = GatewayRoute.model_validate(gateway.request("POST", "/v1/routes", gateway.enrollment_key, {
                "instance_id": instance_id, "device_id": local_id, "platform": platform,
                "environment": body.environment, "token": token,
            }))
        except (httpx.HTTPError, ValueError):
            raise api_error(503, "mobile_push_unavailable", "推送暂时不可用，请稍后重试。") from None
        updated = db.execute(update(MobilePushRoute).where(
            MobilePushRoute.id == local_id, MobilePushRoute.retired_at.is_(None),
        ).values(gateway_route_id=str(reply.route_id), submit_token=reply.submit_token)).rowcount
        if not updated:
            raise api_error(409, "mobile_push_route_changed", "此设备的推送已关闭。")
        db.commit()
        return MobileResponse(data=PushRouteState(
            available=True, route_id=reply.route_id, test_available=True,
        ))

    @router.delete("/push")
    def disable(db: DB, device: Device) -> MobileResponse[PushRouteState]:
        retire_device_push(db, device.id, int(time.time()))
        db.commit()
        return MobileResponse(data=PushRouteState(available=configured_gateway() is not None))

    @router.post("/push/test")
    def send_test(
        db: DB, device: Device, _empty: Annotated[None, Depends(_empty_test_request)],
    ) -> MobileResponse[PushTestState]:
        gateway = configured_gateway()
        if gateway is None:
            raise api_error(503, "mobile_push_test_unavailable", "此服务尚未启用测试通知。")
        device_id, now = device.id, int(time.time())
        instance_id, route_id, submit_token = _reserve_test_route(db, device_id, now)
        event_id = str(uuid5(NAMESPACE_URL, f"panwatch/mobile/push-test/v1/{instance_id}/{device_id}/{now // 60}"))
        try:
            reply = gateway.request("POST", "/v1/test-deliveries", submit_token, {
                "route_id": route_id, "event_id": event_id,
            })
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403, 410):
                raise api_error(409, "mobile_push_route_required", "请重新为此设备开启通知。") from None
            if exc.response.status_code in (404, 405):
                raise api_error(503, "mobile_push_test_unavailable", "当前推送网关不支持测试通知。") from None
            if exc.response.status_code == 429:
                raise api_error(429, "mobile_push_test_rate_limited", "请稍后再试测试通知。") from None
            raise api_error(503, "mobile_push_test_failed", "暂时无法提交测试通知。") from None
        except (httpx.HTTPError, ValueError):
            raise api_error(503, "mobile_push_test_failed", "暂时无法提交测试通知。") from None
        state = reply.get("state")
        if state == "accepted":
            return MobileResponse(data=PushTestState(state="accepted"))
        if state in ("queued", "leased"):
            return MobileResponse(data=PushTestState(state="queued"))
        raise api_error(503, "mobile_push_test_failed", "暂时无法提交测试通知。")

    return router


def push_delivery_states(db: Session, device_id: str, hit_ids: list[int]) -> dict[int, str]:
    if not hit_ids:
        return {}
    return dict(db.execute(select(PriceAlertDelivery.hit_id, PriceAlertDelivery.status).join(
        MobilePushRoute, MobilePushRoute.id == PriceAlertDelivery.mobile_push_route_id,
    ).where(
        PriceAlertDelivery.hit_id.in_(hit_ids), MobilePushRoute.device_id == device_id,
        MobilePushRoute.retired_at.is_(None),
    )).tuples().all())


def push_event(db: Session, device_id: str, event_id: UUID, route_id: UUID):
    return db.execute(select(PriceAlertHit, PriceAlertDelivery.status).join(
        PriceAlertDelivery, PriceAlertDelivery.hit_id == PriceAlertHit.id,
    ).join(MobilePushRoute, MobilePushRoute.id == PriceAlertDelivery.mobile_push_route_id).where(
        PriceAlertDelivery.event_id == str(event_id), MobilePushRoute.device_id == device_id,
        MobilePushRoute.gateway_route_id == str(route_id), MobilePushRoute.retired_at.is_(None),
    )).tuples().one_or_none()
