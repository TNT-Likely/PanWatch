import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from src.bootstrap.application import app
from src.modules.administration.api import auth, settings
from src.modules.market import alert_delivery, data_collector, price_alert_engine
from src.modules.mobile import api as mobile, push
from src.modules.portfolio.api import accounts
from src.platform.marketdata import marketdata_client
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.migrations import (
    _m141_mobile_device_pairing, _m142_mobile_device_renewal, _m144_mobile_push_routes, _m148_mobile_push_test_minute,
)
from src.platform.persistence.models import (
    Account, AgentConfig, AgentRun, DataSource, MobileDevice, MobilePairing, MobilePushRoute, NotifyChannel, Position,
    PriceAlertDelivery, PriceAlertHit, PriceAlertRule, Stock,
)
from src.platform.scheduling import trading_calendar

MOBILE = "/api/mobile/v1"
ORIGIN = "https://test-panwatch.example"
PHONE_KEY = "1" * 64


@pytest.fixture
def instance(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'instance.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    def override():
        with sessions() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, override)
    monkeypatch.setattr(auth, "_jwt_secret", "mobile-api-test-only-secret-serialized")
    try:
        yield TestClient(app), sessions
    finally:
        engine.dispose()


def _owner(sessions):
    with sessions() as db:
        auth.set_password_hash(db, "isolated-test")
    return _bearer(auth.create_token()[0])


def _bearer(value):
    return {"Authorization": f"Bearer {value}"}


def _start(client, owner):
    response = client.post(f"{MOBILE}/pairings", headers=owner, json={"origin": ORIGIN})
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()["data"]


def _claim(client, qr, claim_key=PHONE_KEY, *, renewable=False, manage_alerts=False, read_diagnostics=False, manage_watchlist=False):
    body = {
        "challenge": qr["challenge"],
        "claim_key": claim_key,
        "device_name": "Test iPhone",
        "platform": "ios",
    }
    if renewable:
        body["renewable"] = True
    if manage_alerts:
        body["manage_alerts"] = True
    if read_diagnostics:
        body["read_diagnostics"] = True
    if manage_watchlist:
        body["manage_watchlist"] = True
    return client.post(f"{MOBILE}/pairings/{qr['pairing_id']}/claim", json=body)


def _exchange(client, root, claim_key=PHONE_KEY):
    return client.post(f"{root}/exchange", headers=_bearer(claim_key))


def _approve(client, owner, root, qr, *, renewable=False, manage_alerts=False, read_diagnostics=False, manage_watchlist=False):
    response = _claim(client, qr, renewable=renewable, manage_alerts=manage_alerts, read_diagnostics=read_diagnostics, manage_watchlist=manage_watchlist)
    assert response.status_code == 200, response.text
    code = response.json()["data"]["verification_code"]
    review = client.get(root, headers=owner).json()["data"]
    assert review["verification_code"] == code
    assert review["renewable"] is renewable
    assert review["manage_alerts"] is manage_alerts
    assert review["read_diagnostics"] is read_diagnostics
    assert review["manage_watchlist"] is manage_watchlist
    response = client.post(f"{root}/decision", headers=owner, json={
        "decision": "approve", "verification_code": code,
    })
    assert response.status_code == 200, response.text


def test_pairing_requires_a_configured_login_and_same_https_origin(instance):
    client, sessions = instance
    assert client.get(f"{MOBILE}/capabilities").json()["data"] == {
        "service": "panwatch", "service_version": settings.get_app_version(),
        "protocol_min": 1, "protocol_max": 1, "pairing": False, "password_login": False, "portfolio_read": True,
        "session_refresh": True, "watchlist_read": True, "alerts_manage": True,
        "portfolio_metrics": True, "symbol_facts": True, "diagnostics_read": True,
        "disclosures": True, "disclosure_archive": True, "fact_tasks": True, "symbol_alert_history": True,
        "screening": True, "watchlist_manage": True,
    }
    assert client.post(f"{MOBILE}/pairings", json={"origin": ORIGIN}).status_code == 403
    owner = _owner(sessions)
    assert client.post(f"{MOBILE}/pairings", json={"origin": ORIGIN}).status_code == 401
    assert client.get(f"{MOBILE}/capabilities").json()["data"]["pairing"] is True
    for invalid in ["http://example.test", "https://name@example.test", f"{ORIGIN}/app", f"{ORIGIN}?"]:
        response = client.post(f"{MOBILE}/pairings", headers=owner, json={"origin": invalid})
        assert response.json()["error_code"] == "mobile_https_required"
    response = client.post(
        f"{MOBILE}/pairings", headers={**owner, "Origin": "https://other.example"},
        json={"origin": ORIGIN},
    )
    assert response.json()["error_code"] == "mobile_origin_mismatch"


def test_only_the_approved_phone_can_exchange_once_and_revocation_is_immediate(instance):
    client, sessions = instance
    owner = _owner(sessions)
    qr = _start(client, owner)
    root = f"{MOBILE}/pairings/{qr['pairing_id']}"
    assert _exchange(client, root).status_code == 409
    _approve(client, owner, root, qr)
    replayed_claim = _claim(client, qr, renewable=True).json()["data"]
    assert replayed_claim["status"] == "approved" and not replayed_claim["renewable"]
    assert _claim(client, qr, "2" * 64).status_code == 409
    assert _exchange(client, root, "2" * 64).status_code == 409
    assert client.get(f"{root}/status", headers=owner).status_code == 404
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: _exchange(client, root), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    grant = next(response.json()["data"] for response in responses if response.status_code == 200)
    assert grant["scope"] == "portfolio:read"
    assert grant["refresh_token"] is None and not grant["watchlist_read"]
    assert grant["origin"] == ORIGIN
    with sessions() as db:
        assert len(db.scalars(select(MobileDevice)).all()) == 1
        assert db.get(MobileDevice, grant["device_id"]).token_hash != grant["access_token"]
        pairing = db.get(MobilePairing, qr["pairing_id"])
        assert pairing.challenge_hash != qr["challenge"]
        assert pairing.claim_hash != PHONE_KEY

    device = _bearer(grant["access_token"])
    assert client.get(f"{MOBILE}/portfolio", headers=owner).status_code == 401
    assert client.get(f"{MOBILE}/portfolio", headers=device).json()["data"]["holdings"] == []
    assert client.get(f"{MOBILE}/symbols/watchlist", headers=device).status_code == 403
    assert client.get("/api/accounts", headers=device).status_code == 401
    assert client.post("/api/accounts", headers=device, json={"name": "Forbidden"}).status_code == 401
    assert client.get(f"{MOBILE}/devices", headers=device).status_code == 401
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=owner).status_code == 200
    refused = client.get(f"{MOBILE}/portfolio", headers=device)
    assert refused.status_code == 401
    assert refused.json()["error_code"] == "mobile_device_revoked"


def test_password_login_reuses_web_credentials_and_issues_only_a_revocable_device(instance):
    client, sessions = instance
    with sessions() as db:
        auth.set_stored_username(db, "isolated-user")
        auth.set_password_hash(db, auth.hash_password("test password with spaces!"))
    assert client.get(f"{MOBILE}/capabilities").json()["data"]["password_login"] is True
    credentials = {"username": "isolated-user", "password": "test password with spaces!"}
    web = client.post("/api/auth/login", json=credentials).json()["data"]
    request = {**credentials, "origin": ORIGIN, "device_name": "Manual Android", "platform": "android"}
    refused = client.post(f"{MOBILE}/devices/login", json={**request, "password": "wrong password"})
    assert refused.status_code == 401
    assert refused.json()["error_code"] == "invalid_credentials"
    assert client.get(f"{MOBILE}/devices", headers=_bearer(web["token"])).json()["data"] == []

    response = client.post(f"{MOBILE}/devices/login", json=request)
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    grant = response.json()["data"]
    assert grant["origin"] == ORIGIN and grant["scope"] == "portfolio:read"
    device = _bearer(grant["access_token"])
    assert client.get(f"{MOBILE}/portfolio", headers=device).status_code == 200
    assert client.get("/api/auth/me", headers=device).status_code == 401
    assert client.get(f"{MOBILE}/portfolio", headers=_bearer(web["token"])).status_code == 401
    items = client.get(f"{MOBILE}/devices", headers=_bearer(web["token"])).json()["data"]
    assert items[0]["name"] == "Manual Android"
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=_bearer(web["token"])).status_code == 200
    assert client.get(f"{MOBILE}/portfolio", headers=device).json()["error_code"] == "mobile_device_revoked"


def test_password_login_validation_keeps_passwords_out_of_responses(instance):
    client, _ = instance
    body = {
        "username": "isolated-user", "password": "a fixture password that must not be echoed",
        "device_name": "Phone", "platform": "android",
    }
    response = client.post(f"{MOBILE}/devices/login", json=body)
    assert response.status_code == 422
    assert response.json()["error_code"] == "mobile_login_invalid"
    assert body["password"] not in response.text
    response = client.post(f"{MOBILE}/devices/login", json={**body, "origin": ORIGIN})
    assert response.status_code == 400
    assert response.json()["error_code"] == "auth_not_configured"


@pytest.mark.parametrize("decision,terminal", [("deny", "denied"), ("cancel", "cancelled")])
def test_phone_observes_terminal_web_decisions(instance, decision, terminal):
    client, sessions = instance
    owner = _owner(sessions)
    qr = _start(client, owner)
    root = f"{MOBILE}/pairings/{qr['pairing_id']}"
    code = _claim(client, qr).json()["data"]["verification_code"]
    response = client.post(f"{root}/decision", headers=owner, json={
        "decision": decision, "verification_code": code,
    })
    assert response.status_code == 200
    assert client.get(f"{root}/status", headers=_bearer(PHONE_KEY)).json()["data"]["status"] == terminal
    assert _exchange(client, root).status_code == 409


def test_expired_pairing_does_not_issue_a_device(instance, monkeypatch):
    client, sessions = instance
    owner = _owner(sessions)
    qr = _start(client, owner)
    root = f"{MOBILE}/pairings/{qr['pairing_id']}"
    _approve(client, owner, root, qr)
    monkeypatch.setattr(mobile, "_now", lambda: qr["expires_at"])
    assert client.get(f"{root}/status", headers=_bearer(PHONE_KEY)).json()["data"]["status"] == "expired"
    assert _exchange(client, root).status_code == 409


def test_a_separate_instance_cannot_accept_a_pairing_or_device_grant(instance, tmp_path, monkeypatch):
    client, sessions = instance
    owner = _owner(sessions)
    qr = _start(client, owner)
    root = f"{MOBILE}/pairings/{qr['pairing_id']}"
    _approve(client, owner, root, qr, renewable=True)
    grant = _exchange(client, root).json()["data"]

    other = create_engine(f"sqlite:///{tmp_path / 'other.db'}")
    Base.metadata.create_all(other)
    other_sessions = sessionmaker(bind=other)

    def override():
        with other_sessions() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, override)
    try:
        assert _claim(client, qr).status_code == 404
        assert _exchange(client, root).status_code == 409
        response = client.get(f"{MOBILE}/portfolio", headers=_bearer(grant["access_token"]))
        assert response.status_code == 401
        assert response.json()["error_code"] == "mobile_device_invalid"
        assert _renew(client, grant["refresh_token"], PHONE_KEY).status_code == 401
        assert client.get(f"{MOBILE}/symbols/watchlist", headers=_bearer(grant["access_token"])).status_code == 401
    finally:
        other.dispose()


def test_read_only_portfolio_reports_partial_valuation_and_refreshes_real_positions(instance, monkeypatch):
    client, sessions = instance
    owner = _owner(sessions)
    qr = _start(client, owner)
    root = f"{MOBILE}/pairings/{qr['pairing_id']}"
    _approve(client, owner, root, qr)
    grant = _exchange(client, root).json()["data"]
    device = _bearer(grant["access_token"])
    assert client.get(f"{MOBILE}/portfolio", headers=device).json()["data"] == {
        "total_assets": 0, "currency": "CNY", "valuation": "complete", "holdings": [],
    }
    monkeypatch.setattr(accounts, "get_usd_cny_rate", lambda: 7)
    monkeypatch.setattr(accounts, "md_quote_rows", lambda symbols, market: [
        {"symbol": symbols[0], "current_price": 10},
    ] if market == "CN" else [])
    with sessions() as db:
        account = Account(name="Private account", available_funds=50, enabled=True)
        china = Stock(symbol="SAME", market="CN", name="China fixture")
        america = Stock(symbol="SAME", market="US", name="US fixture")
        db.add_all([account, china, america])
        db.flush()
        china_id = china.id
        db.add_all([
            Position(account_id=account.id, stock_id=china.id, quantity=2, cost_price=99),
            Position(account_id=account.id, stock_id=america.id, quantity=1, cost_price=99),
        ])
        db.commit()
    result = client.get(f"{MOBILE}/portfolio", headers=device).json()["data"]
    assert result == {
        "total_assets": 70, "currency": "CNY", "valuation": "partial", "holdings": [
            {"symbol": "SAME", "name": "China fixture", "market": "CN", "quantity": 2,
             "market_value": 20, "currency": "CNY"},
            {"symbol": "SAME", "name": "US fixture", "market": "US", "quantity": 1,
             "market_value": None, "currency": "USD"},
        ],
    }
    with sessions() as db:
        position = db.scalar(select(Position).where(Position.stock_id == china_id))
        position.quantity = 3
        db.commit()
    assert client.get(f"{MOBILE}/portfolio", headers=device).json()["data"]["total_assets"] == 80


def _renewable_grant(client, sessions, *, manage_alerts=False, read_diagnostics=False, manage_watchlist=False):
    owner = _owner(sessions)
    qr = _start(client, owner)
    root = f"{MOBILE}/pairings/{qr['pairing_id']}"
    _approve(client, owner, root, qr, renewable=True, manage_alerts=manage_alerts, read_diagnostics=read_diagnostics, manage_watchlist=manage_watchlist)
    return owner, _exchange(client, root).json()["data"]


def _renew(client, token, key):
    return client.post(f"{MOBILE}/session/refresh", headers={**_bearer(token), "Idempotency-Key": key})


def test_device_renewal_replays_only_the_issued_grant_after_a_lost_response(instance, monkeypatch):
    client, sessions = instance
    owner, grant = _renewable_grant(client, sessions)
    now = grant["expires_at"] + 1
    monkeypatch.setattr(mobile, "_now", lambda: now)
    assert client.get(f"{MOBILE}/portfolio", headers=_bearer(grant["access_token"])).json()["error_code"] == "mobile_device_expired"
    assert _renew(client, grant["access_token"], PHONE_KEY).status_code == 401
    assert _renew(client, grant["refresh_token"], "bad-key").status_code == 400

    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: _renew(client, grant["refresh_token"], PHONE_KEY), range(2)))
    assert [r.status_code for r in replies] == [200, 200]
    assert replies[0].headers["Cache-Control"] == "no-store"
    next_grant = replies[0].json()["data"]
    assert next_grant == replies[1].json()["data"]
    for field in ("instance_id", "device_id", "origin"):
        assert next_grant[field] == grant[field]
    assert next_grant["access_token"] != grant["access_token"]
    assert next_grant["refresh_token"] != grant["refresh_token"]
    assert client.get(f"{MOBILE}/portfolio", headers=_bearer(next_grant["refresh_token"])).status_code == 401
    assert client.get(f"{MOBILE}/portfolio", headers=_bearer(next_grant["access_token"])).status_code == 200
    assert _renew(client, grant["refresh_token"], "2" * 64).status_code == 401
    with sessions() as db:
        device = db.get(MobileDevice, grant["device_id"])
        assert device.refresh_token_hash == mobile._digest(next_grant["refresh_token"])
        assert device.refresh_request_hash == mobile._digest(PHONE_KEY)

    now += mobile._REPLAY_TTL
    assert _renew(client, grant["refresh_token"], PHONE_KEY).status_code == 401
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=owner).status_code == 200
    assert _renew(client, next_grant["refresh_token"], "2" * 64).json()["error_code"] == "mobile_device_revoked"


def test_different_renewal_requests_use_the_credential_only_once(instance):
    client, sessions = instance
    _, grant = _renewable_grant(client, sessions)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda nonce: _renew(client, grant["refresh_token"], nonce), (PHONE_KEY, "2" * 64)))
    assert sorted(r.status_code for r in replies) == [200, 401]
    assert client.get(f"{MOBILE}/portfolio", headers=_bearer(grant["access_token"])).status_code == 401


def test_password_grant_reads_watchlist_in_web_order_without_account_fields(instance):
    client, sessions = instance
    with sessions() as db:
        auth.set_stored_username(db, "isolated-user")
        auth.set_password_hash(db, auth.hash_password("isolated-password"))
        db.add_all([
            Stock(symbol="SAME", market="CN", name="China fixture", sort_order=2),
            Stock(symbol="SAME", market="US", name="US fixture", sort_order=1),
        ])
        db.commit()
    response = client.post(f"{MOBILE}/devices/login", json={
        "username": "isolated-user", "password": "isolated-password",
        "origin": ORIGIN, "device_name": "Manual Android", "platform": "android", "renewable": True,
    })
    assert response.status_code == 201
    grant = response.json()["data"]
    assert grant["refresh_token"] and grant["watchlist_read"]
    path = f"{MOBILE}/symbols/watchlist"
    device = _bearer(grant["access_token"])
    assert client.get(path, headers=device).json()["data"] == [
        {"symbol": "SAME", "market": "US", "name": "US fixture"},
        {"symbol": "SAME", "market": "CN", "name": "China fixture"},
    ]
    assert client.get("/api/stocks", headers=device).status_code == 401
    assert client.post(path, headers=device, json={"symbol": "NEW", "market": "US"}).status_code == 405


def test_mobile_alerts_replay_real_engine_and_keep_web_rules_and_channels(instance, monkeypatch):
    client, sessions = instance
    _, old = _renewable_grant(client, sessions)
    assert client.get(f"{MOBILE}/alerts", headers=_bearer(old["access_token"])).status_code == 403
    owner, grant = _renewable_grant(client, sessions, manage_alerts=True)
    assert grant["alerts_manage"] is True
    device = _bearer(grant["access_token"])
    with sessions() as db:
        stock = Stock(symbol="PWTST", market="CN", name="QA fixture")
        db.add(stock)
        db.add(NotifyChannel(name="Existing Web channel", type="webhook", config={}, enabled=True, is_default=True))
        db.flush()
        group = {"op": "or", "items": [
            {"type": "price", "op": ">", "value": 200},
            {"type": "volume", "op": ">=", "value": 2000},
        ]}
        original = PriceAlertRule(stock_id=stock.id, name="Web multi-condition", condition_group=group, enabled=True)
        db.add(original)
        db.commit()
        web_rule_id = original.id

    reply = client.post(f"{MOBILE}/alerts/{web_rule_id}/enabled", headers=device, json={"enabled": False})
    assert reply.status_code == 200
    assert reply.json()["data"]["condition_group"] == group
    body = {"market": "CN", "symbol": "PWTST", "comparison": "<", "price": 100, "request_id": str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: client.post(f"{MOBILE}/alerts", headers=device, json=body), range(2)))
    assert [r.status_code for r in replies] == [201, 201]
    saved = replies[0].json()["data"]
    assert saved == replies[1].json()["data"]
    assert client.post(f"{MOBILE}/alerts", headers=device, json={**body, "price": 101}).status_code == 409
    assert client.get("/api/price-alerts", headers=device).status_code == 401

    engine = price_alert_engine.PriceAlertEngine()
    monkeypatch.setattr(price_alert_engine, "SessionLocal", sessions)
    monkeypatch.setattr(price_alert_engine, "quote_date_is_current", lambda market, quote: True)
    monkeypatch.setattr(price_alert_engine, "_is_trading_time", lambda market: True)
    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda market: True)

    async def replay(stocks):
        return {("CN", "PWTST"): {"current_price": 99.70, "source": "qa_replay"}}

    monkeypatch.setattr(engine, "_fetch_quotes_map", replay)
    fired = asyncio.run(engine.scan_once(only_rule_id=saved["id"]))
    assert fired["triggered"] == 1, fired
    event_id = fired["items"][0]["hit_id"]
    event = client.get(f"{MOBILE}/events/{event_id}", headers=device).json()["data"]
    assert event["id"] == event_id and event["rule_id"] == saved["id"]
    assert event["observed_price"] == 99.70 and event["quote_source"] == "qa_replay"
    assert event["conditions"] == [{"type": "price", "op": "<", "target": 100, "actual": 99.70, "matched": True}]
    assert client.get(f"{MOBILE}/events", headers=device).json()["data"] == [event]
    with sessions() as db:
        assert db.get(PriceAlertRule, web_rule_id).condition_group == group
        deliveries = db.scalars(select(PriceAlertDelivery).where(PriceAlertDelivery.hit_id == event_id)).all()
        assert all(row.channel_id is None for row in deliveries)
        other_stock = Stock(symbol="PWTST", market="US", name="US fixture")
        other_rule = PriceAlertRule(stock=other_stock, name="Same code in another market", condition_group=group)
        later = datetime.fromisoformat(event["occurred_at"]).replace(tzinfo=None) + timedelta(minutes=1)
        other_event = PriceAlertHit(stock=other_stock, rule=other_rule, trigger_time=later)
        db.add(other_event)
        db.commit()
        other_id = other_event.id

    history = f"{MOBILE}/events?limit=1"
    assert client.get(history, headers=device).json()["data"][0]["id"] == other_id
    assert client.get(f"{history}&market=CN&symbol=PWTST", headers=device).json()["data"] == [event]
    assert client.get(f"{history}&market=US&symbol=PWTST", headers=device).json()["data"][0]["id"] == other_id
    assert client.get(f"{history}&market=CN", headers=device).status_code == 422
    assert client.get(f"{history}&market=CN&symbol=PWTST", headers=_bearer(old["access_token"])).status_code == 403

    rotated = _renew(client, grant["refresh_token"], "9" * 64).json()["data"]
    assert rotated["alerts_manage"] is True
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=owner).status_code == 200
    assert client.get(f"{MOBILE}/events/{event_id}", headers=_bearer(rotated["access_token"])).status_code == 401


def test_push_route_owns_opaque_event_and_retires_with_device(instance, monkeypatch):
    client, sessions = instance
    owner, grant = _renewable_grant(client, sessions, manage_alerts=True)
    _, other = _renewable_grant(client, sessions, manage_alerts=True)
    device = _bearer(grant["access_token"])
    path = f"{MOBILE}/push"
    assert client.get(path, headers=device).json()["data"] == {
        "available": False, "route_id": None, "test_available": False,
    }

    monkeypatch.setenv("PANWATCH_MOBILE_PUSH_GATEWAY_URL", "https://push.example")
    monkeypatch.setenv("PANWATCH_MOBILE_PUSH_ENROLLMENT_KEY", "k" * 43)
    aliases = {}
    relay_calls = []
    withdrawn = []
    lose_first_reply = True

    def gateway_request(self, method, endpoint, bearer, body):
        nonlocal lose_first_reply
        if endpoint == "/v1/routes":
            assert set(body) == {"instance_id", "device_id", "platform", "environment", "token"}
            assert body["instance_id"] == grant["instance_id"] and body["device_id"] != grant["device_id"]
            route = aliases.setdefault(body["device_id"], str(uuid4()))
            if lose_first_reply:
                lose_first_reply = False
                raise httpx.ReadTimeout("simulated dropped gateway response")
            return {"route_id": route, "submit_token": "s" * 43}
        if endpoint == "/v1/enrollments/revoke":
            assert set(body) == {"instance_id", "device_id"}
            withdrawn.append(body["device_id"])
            return {"state": "revoked"}
        assert endpoint == "/v1/deliveries" and set(body) == {"route_id", "event_id"}
        relay_calls.append(body)
        return {"state": "accepted" if len(relay_calls) > 1 else "queued"}

    monkeypatch.setattr(push.Gateway, "request", gateway_request)
    body = {"token": "example-native-token", "environment": "sandbox"}
    assert client.put(path, headers=device, json=body).status_code == 503
    route = client.put(path, headers=device, json=body).json()["data"]["route_id"]
    assert client.put(path, headers=device, json={**body, "token": "rotated"}).json()["data"]["route_id"] == route
    assert len(aliases) == 1

    with sessions() as db:
        stock = Stock(symbol="PUSHTST", market="US", name="Synthetic")
        db.add(stock)
        db.flush()
        rule = PriceAlertRule(
            stock_id=stock.id, name="Synthetic rule", market_hours_mode="always", mobile_request_id=str(uuid4()),
        )
        db.add(rule)
        db.commit()
        rule_id = rule.id
    monkeypatch.setattr(price_alert_engine, "SessionLocal", sessions)
    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda market: True)
    hit_id = price_alert_engine.PriceAlertEngine()._persist_hit(
        rule_id, price_alert_engine._utc_now(), {"quote": {"current_price": 99.70}}, 99.70,
    )
    assert hit_id is not None
    worker = alert_delivery.AlertDeliveryWorker(sessions)
    assert asyncio.run(worker.drain())["processed"] == 1
    with sessions() as db:
        delivery = db.query(PriceAlertDelivery).filter_by(hit_id=hit_id).one()
        assert delivery.content == "" and delivery.status == "retry"
        event_id = delivery.event_id
        delivery.next_attempt_at = alert_delivery.now_utc()
        db.commit()
    assert asyncio.run(worker.drain())["processed"] == 1
    assert relay_calls == [{"route_id": route, "event_id": event_id}] * 2

    event_path = f"{path}/events/{event_id}?route_id={route}"
    event = client.get(event_path, headers=device).json()["data"]
    assert event["id"] == hit_id and event["observed_price"] == 99.70
    assert event["push_delivery"] == "submitted"
    assert client.get(f"{MOBILE}/events/{hit_id}", headers=device).json()["data"] == event
    assert client.get(event_path, headers=_bearer(other["access_token"])).status_code == 404
    assert client.delete(path, headers=device).status_code == 200
    assert client.get(event_path, headers=device).status_code == 404
    push.withdraw_pending(sessions)
    assert len(withdrawn) == 1

    new_route = client.put(path, headers=device, json=body).json()["data"]["route_id"]
    assert new_route != route
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=owner).status_code == 200
    assert client.get(event_path, headers=device).status_code == 401
    push.withdraw_pending(sessions)
    assert len(set(withdrawn)) == 2
    with sessions() as db:
        assert all(row.submit_token is None and row.withdraw_after is None for row in db.query(MobilePushRoute))


def test_push_test_is_device_scoped_and_idempotent_across_route_changes(instance, monkeypatch):
    client, sessions = instance
    owner, grant = _renewable_grant(client, sessions, manage_alerts=True)
    _, other = _renewable_grant(client, sessions, manage_alerts=True)
    _, read_only = _renewable_grant(client, sessions)
    device = _bearer(grant["access_token"])
    second = _bearer(other["access_token"])
    path = f"{MOBILE}/push"
    registration = {"token": "synthetic-device-token", "environment": "sandbox"}
    clock = [int(push.time.time()) // 60 * 60 + 1]
    monkeypatch.setattr(push, "time", SimpleNamespace(time=lambda: clock[0]))
    network = push.Gateway("https://push.example", "synthetic-enrollment-fixture")
    gateway_enabled = False
    monkeypatch.setattr(push, "configured_gateway", lambda: network if gateway_enabled else None)
    registered = {}
    sent = []
    known_events = set()
    old_gateway = False

    def gateway_request(self, method, endpoint, bearer, body):
        assert self is network and method == "POST"
        if endpoint == "/v1/routes":
            route_id = str(uuid4())
            submit_token = f"{len(registered) + 1:043d}"
            registered[route_id] = submit_token
            return {"route_id": route_id, "submit_token": submit_token}
        assert endpoint == "/v1/test-deliveries" and set(body) == {"route_id", "event_id"}
        assert bearer == registered[body["route_id"]]
        if old_gateway:
            request = httpx.Request("POST", f"https://push.example{endpoint}")
            raise httpx.HTTPStatusError("unsupported", request=request, response=httpx.Response(404, request=request))
        sent.append(body)
        key = (body["route_id"], body["event_id"])
        state = "accepted" if key in known_events else "queued"
        known_events.add(key)
        return {"state": state}

    monkeypatch.setattr(push.Gateway, "request", gateway_request)

    assert client.post(f"{path}/test").status_code == 401
    assert client.post(f"{path}/test", headers=_bearer(read_only["access_token"])).status_code == 403
    assert client.post(f"{path}/test", headers=device).status_code == 503
    gateway_enabled = True
    first = client.put(path, headers=device, json=registration).json()["data"]
    assert first["available"] is True and first["test_available"] is True
    assert client.get(path, headers=device).json()["data"]["test_available"] is True
    assert client.get(path, headers=second).json()["data"]["test_available"] is False
    assert client.post(f"{path}/test", headers=second).status_code == 409
    assert client.post(f"{path}/test", headers=device, json={"route_id": str(uuid4())}).status_code == 400
    assert client.post(f"{path}/test", headers=device, params={"route_id": str(uuid4())}).status_code == 400
    assert not sent

    queued = client.post(f"{path}/test", headers=device)
    assert queued.status_code == 200 and queued.json()["data"] == {"state": "queued"}
    assert queued.headers["Cache-Control"] == "no-store"
    accepted = client.post(f"{path}/test", headers=device)
    assert accepted.status_code == 200 and accepted.json()["data"] == {"state": "accepted"}
    assert sent[0] == sent[1]
    assert sent[0]["route_id"] == first["route_id"]
    other_route = client.put(path, headers=second, json=registration).json()["data"]["route_id"]
    assert client.post(f"{path}/test", headers=second).json()["data"] == {"state": "queued"}
    assert sent[2]["route_id"] == other_route and sent[2]["event_id"] != sent[1]["event_id"]

    assert client.delete(path, headers=device).json()["data"]["test_available"] is False
    new_route = client.put(path, headers=device, json=registration).json()["data"]["route_id"]
    assert new_route != first["route_id"]
    limited = client.post(f"{path}/test", headers=device)
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "59"
    assert len(sent) == 3
    clock[0] += 60
    assert client.post(f"{path}/test", headers=device).json()["data"] == {"state": "queued"}
    assert sent[3]["route_id"] == new_route and sent[3]["event_id"] != sent[1]["event_id"]
    old_gateway = True
    unavailable = client.post(f"{path}/test", headers=device)
    assert unavailable.status_code == 503 and unavailable.json()["error_code"] == "mobile_push_test_unavailable"
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=owner).status_code == 200
    assert client.post(f"{path}/test", headers=device).status_code == 401
    assert len(sent) == 4


def test_mobile_migration_is_additive_and_safe_to_repeat(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'older.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO accounts VALUES (1, 'Legacy holding account')"))
        _m141_mobile_device_pairing(conn)
        _m141_mobile_device_pairing(conn)
        assert conn.scalar(text("SELECT name FROM accounts WHERE id = 1")) == "Legacy holding account"
        for table in ("mobile_installations", "mobile_pairings", "mobile_devices"):
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        conn.execute(text("""
            INSERT INTO mobile_devices (id, name, platform, token_hash, scope, created_at, expires_at)
            VALUES ('device', 'Old device', 'ios', 'legacy-fixture-hash', 'portfolio:read', 1, 604801)
        """))
        _m142_mobile_device_renewal(conn)
        _m142_mobile_device_renewal(conn)
        legacy = conn.execute(text("SELECT scope, expires_at, refresh_token_hash FROM mobile_devices")).one()
        assert tuple(legacy) == ("portfolio:read", 604801, None)
        conn.execute(text("CREATE TABLE price_alert_deliveries (id INTEGER PRIMARY KEY, event_id VARCHAR)"))
        conn.execute(text("INSERT INTO price_alert_deliveries VALUES (1, 'web-event')"))
        _m144_mobile_push_routes(conn)
        _m144_mobile_push_routes(conn)
        conn.execute(text("""
            INSERT INTO mobile_push_routes (id, device_id, instance_id, withdraw_attempts)
            VALUES ('legacy-route', 'device', 'instance', 0)
        """))
        _m148_mobile_push_test_minute(conn)
        _m148_mobile_push_test_minute(conn)
        route = conn.execute(text("SELECT id, test_minute FROM mobile_push_routes")).one()
        assert tuple(route) == ("legacy-route", None)
        channel = conn.execute(text("SELECT event_id, mobile_push_route_id FROM price_alert_deliveries WHERE id = 1")).one()
        assert tuple(channel) == ("web-event", None)
        assert conn.scalar(text("SELECT name FROM accounts WHERE id = 1")) == "Legacy holding account"
    engine.dispose()


def test_portfolio_metrics_use_same_device_scope_and_preserve_original_response(instance, monkeypatch):
    client, sessions = instance
    owner, grant = _renewable_grant(client, sessions)
    device = _bearer(grant["access_token"])
    summary = {
        "accounts": [{
            "id": 6, "name": "Test", "total_assets": 50, "total_pnl": 3, "total_daily_pnl": 0,
            "positions": [{
                "symbol": "TEST", "name": "Synthetic", "market": "US", "quantity": 2,
                "market_value": 4, "daily_pnl": 0, "private_note": "fixture only",
            }],
        }],
        "total": {"total_assets": 50, "total_pnl": 3, "total_daily_pnl": 0},
    }
    monkeypatch.setattr(accounts, "get_portfolio_summary", lambda **kwargs: summary)
    metrics_path = f"{MOBILE}/portfolio/metrics"
    assert client.get(metrics_path).status_code == 401
    assert client.get(metrics_path, headers=owner).status_code == 401
    result = client.get(metrics_path, headers=device)
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    assert result.json()["data"]["accounts"][0]["unrealized_pnl"] == {"value": 3, "coverage": "complete"}
    assert "private_note" not in result.text
    original = client.get(f"{MOBILE}/portfolio", headers=device).json()["data"]
    assert set(original) == {"total_assets", "currency", "valuation", "holdings"}
    assert original["holdings"][0]["currency"] == "USD"
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=owner).status_code == 200
    assert client.get(metrics_path, headers=device).status_code == 401


def test_diagnostics_preserve_explicit_scope_and_hide_provider_internals(instance, monkeypatch):
    client, sessions = instance
    _, old = _renewable_grant(client, sessions)
    owner, grant = _renewable_grant(client, sessions, read_diagnostics=True)
    assert not old["diagnostics_read"] and grant["diagnostics_read"]
    base = f"{MOBILE}/diagnostics"
    device = _bearer(grant["access_token"])
    assert client.get(base, headers=_bearer(old["access_token"])).status_code == 403
    sentinel = "DIAGNOSTICS_PRIVATE_FIXTURE"
    with sessions() as db:
        agent = AgentConfig(name="diagnostic-fixture", display_name="Synthetic agent", config={"prompt": sentinel})
        db.add(agent)
        db.add(AgentRun(agent_name=agent.name, status="failed", error=sentinel, result=sentinel))
        source = DataSource(name="Synthetic source", type="quote", provider="fixture", config={"secret": sentinel})
        db.add(source)
        db.commit()
        source_id = source.id

    monkeypatch.setattr(marketdata_client, "get_market_data", lambda: SimpleNamespace(health=lambda: {
        "fixture": {"count": 4, "success_rate": 0.75, "last_success_at": 1_700_000_000, "last_error": sentinel},
    }))
    probes = []

    async def probe(source):
        probes.append(source.id)
        return SimpleNamespace(success=False, duration_ms=7, error=sentinel)

    monkeypatch.setattr(data_collector, "get_collector_manager", lambda: SimpleNamespace(test_source=probe))
    response = client.get(base, headers=device)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert sentinel not in response.text
    data = response.json()["data"]
    assert data["sources"][0]["provider_success_rate"] == 0.75
    assert data["agents"][0]["label"] == "Synthetic agent" and data["agents"][0]["result"] == "failed"
    path = f"{base}/sources/{source_id}/test"
    assert client.post(path, headers=owner).status_code == 401 and not probes
    tested = client.post(path, headers=device)
    assert tested.status_code == 200 and sentinel not in tested.text
    assert tested.json()["data"]["passed"] is False and tested.json()["data"]["duration_ms"] == 7
    assert probes == [source_id]
    rotated = _renew(client, grant["refresh_token"], "e" * 64).json()["data"]
    assert rotated["diagnostics_read"] is True
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=owner).status_code == 200
    assert client.post(path, headers=_bearer(rotated["access_token"])).status_code == 401


def test_symbol_facts_keep_market_identity_and_aggregate_real_bar_periods(instance, monkeypatch):
    from datetime import date, timedelta
    from marketdata.types import Bar
    from src.modules.mobile import symbol_facts

    client, sessions = instance
    _, current = _renewable_grant(client, sessions)
    with sessions() as db:
        db.add_all([
            Stock(symbol="SAME", market="US", name="US security"),
            Stock(symbol="SAME", market="CN", name="CN security"),
        ])
        db.commit()

    def quote_rows(symbols, market):
        assert symbols == ["SAME"]
        return [
            {"symbol": "SAME", "market": "US", "current_price": 125, "source": "us-fixture", "quote_date": "2026-09-24", "legacy_advice": "fixture advice"},
            {"symbol": "SAME", "market": "CN", "current_price": 18, "source": "cn-fixture", "quote_date": "2026-09-24"},
        ]

    def bars(symbol, *, market, days):
        price = 100 if market == "US" else 10
        data = [
            Bar((date(2026, 9, 1) + timedelta(days=index)).isoformat(), price, price + index, price + index + 1, price - 1)
            for index in range(24)
        ]
        return data, f"{market.lower()}-bars"

    monkeypatch.setattr(symbol_facts, "md_quote_rows", quote_rows)
    monkeypatch.setattr(symbol_facts, "get_market_data", lambda: SimpleNamespace(klines_with_vendor=bars))
    device = _bearer(current["access_token"])
    path = f"{MOBILE}/symbols"
    assert client.get(f"{path}/US/SAME").status_code == 401
    assert client.get(f"{path}/HK/SAME", headers=device).status_code == 404
    assert client.get(f"{path}/US/SAME?interval=hour", headers=device).status_code == 422
    us = client.get(f"{path}/US/SAME", headers=device)
    assert us.status_code == 200 and "fixture advice" not in us.text
    result = us.json()["data"]
    assert result["target"]["market"] == "US" and result["currency"] == "USD"
    assert result["quote"]["price"] == 125 and result["quote"]["source"] == "us-fixture"
    assert result["bars_source"] == "us-bars" and len(result["bars"]) == 24
    assert result["sma20_daily"] == 113.5
    cn = client.get(f"{path}/CN/SAME?interval=1w", headers=device).json()["data"]
    assert cn["target"]["market"] == "CN" and cn["currency"] == "CNY"
    assert cn["quote"]["price"] == 18 and cn["bars_source"] == "cn-bars"
    assert len(cn["bars"]) == 4 and cn["bars"][-1]["close"] == 33


def test_disclosures_project_only_typed_facts_and_verified_source_links(instance, monkeypatch):
    from datetime import datetime
    from marketdata.types import EventItem
    from src.modules.mobile import disclosures

    client, sessions = instance
    _, grant = _renewable_grant(client, sessions)
    with sessions() as db:
        db.add_all([
            Stock(symbol="600999", market="CN", name="Synthetic issuer"),
            Stock(symbol="600999", market="US", name="Same code other market"),
        ])
        db.commit()

    url = "https://data.eastmoney.com/notices/detail/600999/AN1234567890.html"
    legacy = "FIXTURE BUY RECOMMENDATION TARGET PRICE"
    published = datetime(2026, 11, 20, 10)
    observed = []

    def supplied(symbols, *, market, since_days):
        observed.append((symbols, market, since_days))
        events = [
            EventItem("eastmoney", "AN1234567890", "earnings", legacy, published, ["600999"], 3, url),
            EventItem("eastmoney", "AN1234567891", "earnings", legacy, published, ["600999"], 3, "https://invalid.example/confidential"),
            EventItem("eastmoney", "AN1234567892", "unclassified", legacy, published, ["600999"], 3, url),
        ]
        return events, len(observed) == 1

    monkeypatch.setattr(disclosures, "get_market_data", lambda: SimpleNamespace(events_with_status=supplied))
    auth = _bearer(grant["access_token"])
    route = f"{MOBILE}/facts/disclosures"
    assert client.get(route).status_code == 401
    listed = client.get(route, headers=auth)
    assert listed.status_code == 200
    assert legacy not in listed.text and "invalid.example" not in listed.text
    info = listed.json()["data"]
    assert info["state"] == "ready" and len(info["items"]) == 1
    item = info["items"][0]
    assert item["target"]["market"] == "CN" and item["id"] == "600999:AN1234567890"
    assert item["date"] == "2026-11-20" and item["kind"] == "earnings" and item["url"] == url
    filtered = client.get(f"{route}?symbol=600999&market=CN&days=30", headers=auth).json()["data"]
    assert filtered == {"state": "upstream_unavailable", "items": [item]}
    assert observed == [(["600999"], "CN", 7), (["600999"], "CN", 30)]
    assert client.get(f"{route}?market=US&symbol=600999", headers=auth).json()["data"]["state"] == "market_unavailable"


def test_disclosure_archive_projects_saved_facts_by_date_agent_and_market(instance):
    from src.platform.persistence.models import AgentContextRun

    client, sessions = instance
    _, grant = _renewable_grant(client, sessions)
    legacy = "FIXTURE LEGACY REPORT TARGET PRICE"
    day = "2026-11-20"
    article = "AN202611200001"
    observation = {
        "time": f"{day} 10:15", "source": "eastmoney", "external_id": article, "event_type": "dividend",
        "url": f"https://data.eastmoney.com/notices/detail/600999/{article}.html",
        "title": legacy, "body": legacy,
    }
    context = {"events": [observation, {**observation, "time": "invalid"},
                          {**observation, "url": "https://other.example/report"}], "analysis": legacy}
    with sessions() as db:
        db.add_all([
            Stock(symbol="600999", market="CN", name="Synthetic issuer"),
            AgentContextRun(agent_name="daily_report", stock_symbol="*", analysis_date=day, context_payload={
                "symbols": {"CN:600999": context, "US:600999": context}, "result": legacy,
            }),
            AgentContextRun(agent_name="premarket_outlook", stock_symbol="*", analysis_date=day,
                            context_payload={"content": legacy, "symbols": {"US:600999": context}}),
            AgentContextRun(agent_name="intraday_monitor", stock_symbol="*", analysis_date="2026-11-21",
                            context_payload={"symbols": {"CN:600999": context}}),
        ])
        db.commit()
    route = f"{MOBILE}/facts/disclosures/archive?on={day}"
    auth = _bearer(grant["access_token"])
    assert grant["disclosure_archive"] is True
    assert client.get(route).status_code == 401
    response = client.get(f"{route}&agent=daily_report&market=CN&symbol=600999", headers=auth)
    assert response.status_code == 200
    assert legacy not in response.text and "other.example" not in response.text
    assert response.json()["data"] == {"state": "ready", "items": [{
        "id": f"600999:{article}", "target": {"name": "Synthetic issuer", "symbol": "600999", "market": "CN"},
        "kind": "dividend", "date": day, "source": "eastmoney", "url": observation["url"],
    }]}
    for mismatch in ("agent=premarket_outlook", "agent=intraday_monitor", "symbol=000001", "market=US"):
        assert client.get(f"{route}&{mismatch}", headers=auth).json()["data"]["items"] == []
    assert client.get(f"{route}&agent=tradingagents", headers=auth).status_code == 422


def test_fact_task_resumes_same_id_and_preserves_device_bound_history(instance, monkeypatch):
    from marketdata.types import EventItem
    from src.modules.mobile import disclosures, fact_tasks
    from src.platform.persistence.models import MobileDevice, MobileFactTask

    client, sessions = instance
    owner, device = _renewable_grant(client, sessions)
    _, other = _renewable_grant(client, sessions)
    auth = _bearer(device["access_token"])
    outsider = _bearer(other["access_token"])
    task_id = str(uuid4())
    route = f"{MOBILE}/facts/tasks/{task_id}"
    body = {"phase": "premarket", "target": {"market": "CN", "symbol": "600999"}}
    legacy = "TEST LEGACY RECOMMENDATION BODY"
    with sessions() as db:
        db.add(Stock(symbol="600999", market="CN", name="Fact fixture"))
        db.commit()

    captures = []
    source_reads = []
    capture = fact_tasks._capture
    monkeypatch.setattr(fact_tasks, "_capture", lambda *args: captures.append(args))
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.put(route, headers=auth, json=body), range(2)))
    assert [reply.status_code for reply in responses] == [200, 200]
    assert {reply.json()["data"]["state"] for reply in responses} == {"running"}
    assert len(captures) == 1
    assert client.get(route).status_code == 401
    assert client.get(route, headers=outsider).status_code == 404
    assert client.put(route, headers=outsider, json=body).status_code == 404
    with sessions() as db:
        db.get(MobileDevice, other["device_id"]).scope = "watchlist:read"
        db.commit()
    assert client.get(f"{MOBILE}/facts/tasks", headers=outsider).status_code == 403
    assert client.put(f"{MOBILE}/facts/tasks/{uuid4()}", headers=outsider, json={"phase": "postmarket"}).status_code == 403
    with sessions() as db:
        db.get(MobileDevice, other["device_id"]).scope = "portfolio:read watchlist:read"
        db.commit()
    assert client.put(route, headers=auth, json={"phase": "intraday"}).status_code == 409
    assert client.get(f"{MOBILE}/facts/tasks?market=CN", headers=auth).status_code == 422

    def announced(symbols, *, market, since_days):
        source_reads.append((symbols, market, since_days))
        return [EventItem("eastmoney", "AN202611200001", "dividend", legacy, datetime(2026, 11, 20),
                          ["600999"], 3, "https://data.eastmoney.com/notices/detail/600999/AN202611200001.html")], True

    monkeypatch.setattr(disclosures, "get_market_data", lambda: SimpleNamespace(events_with_status=announced))
    with sessions() as db:
        db.get(MobileFactTask, task_id).lease_until = 0
        db.commit()
    assert client.get(route, headers=auth).json()["data"]["state"] == "retryable"
    assert not source_reads
    monkeypatch.setattr(fact_tasks, "_capture", capture)
    assert client.put(route, headers=auth, json=body).status_code == 200
    reply = client.get(route, headers=auth)
    result = reply.json()["data"]
    assert result["id"] == task_id and result["state"] == "complete"
    assert legacy not in reply.text
    assert result["result"]["disclosures"]["items"][0]["kind"] == "dividend"
    assert client.put(route, headers=auth, json=body).json()["data"] == result
    capture(*captures[0])
    assert source_reads == [(["600999"], "CN", 7)]
    filtered = client.get(
        f"{MOBILE}/facts/tasks?phase=premarket&market=CN&symbol=600999&on={result['created_on']}", headers=auth,
    ).json()["data"]
    assert [row["id"] for row in filtered] == [task_id] and "result" not in filtered[0]
    assert client.get(f"{MOBILE}/facts/tasks", headers=outsider).json()["data"] == []
    assert client.delete(f"{MOBILE}/devices/{device['device_id']}", headers=owner).status_code == 200
    assert client.get(route, headers=auth).status_code == 401


def test_fact_snapshots_keep_stale_and_missing_data_explicit(instance, monkeypatch):
    from src.modules.mobile import fact_snapshots

    client, sessions = instance
    _, device = _renewable_grant(client, sessions)
    auth = _bearer(device["access_token"])
    with sessions() as db:
        db.add_all([Stock(symbol="SAME", market="CN", name="CN quote"), Stock(symbol="SAME", market="US", name="US quote")])
        db.commit()
    monkeypatch.setattr(fact_snapshots, "md_quote_rows", lambda symbols, market: [{
        "symbol": "SAME", "market": "CN", "current_price": 32.5,
        "quote_date": "2026-10-01", "change_pct": 3, "source": "fixture-book",
    }] if market == "CN" else [])

    route = f"{MOBILE}/facts/tasks/{uuid4()}"
    assert client.put(route, headers=auth, json={"phase": "intraday"}).status_code == 200
    snapshot = client.get(route, headers=auth).json()["data"]["result"]
    assert snapshot["phase"] == "intraday" and snapshot["state"] == "partial"
    by_market = {row["target"]["market"]: row for row in snapshot["observations"]}
    assert by_market["CN"]["quote"]["source"] == "fixture-book"
    assert by_market["CN"]["quote"]["date"] == "2026-10-01"
    assert by_market["US"]["quote"] is None
    invalid = f"{MOBILE}/facts/tasks/{uuid4()}"
    assert client.put(invalid, headers=auth, json={
        "phase": "postmarket", "target": {"market": "HK", "symbol": "SAME"},
    }).status_code == 404
    assert client.get(invalid, headers=auth).status_code == 404
    snapshot = fact_snapshots._postmarket({"accounts": [{"positions": [
        {"market": "CN", "symbol": "SAME", "name": "CN quote", "daily_pnl": 8.0},
        {"market": "US", "symbol": "SAME", "name": "US quote", "daily_pnl": None},
    ]}], "total": {"total_daily_pnl": 8.0}}, None)
    assert snapshot.state == "partial"
    assert snapshot.total_daily_pnl.value is None
    assert [item.daily_pnl.value for item in snapshot.contributions] == [8.0, None]


def test_screening_is_numeric_and_watchlist_add_is_explicit_and_idempotent(instance, monkeypatch):
    from src.modules.mobile import screening

    client, sessions = instance
    owner, viewer = _renewable_grant(client, sessions)
    _, editor = _renewable_grant(client, sessions, manage_watchlist=True)
    rows = [
        {"symbol": "EXP", "name": "Higher price", "market": "US"},
        {"symbol": "SAME", "name": "Matching synthetic symbol", "market": "US"},
        {"symbol": "NODATA", "name": "Unavailable quote", "market": "US"},
        {"symbol": "SAME", "name": "Different market", "market": "CN"},
    ]
    def search(term, market, limit):
        if term == "SAME":
            return [row for row in rows if row["symbol"] == term]
        return rows

    monkeypatch.setattr(screening, "search_stocks", search)
    monkeypatch.setattr(screening, "md_quote_rows", lambda *_: [
        {"symbol": "EXP", "market": "US", "current_price": 50},
        {"symbol": "SAME", "market": "US", "current_price": 12, "source": "fixture-quote", "quote_date": "2026-11-20"},
        {"symbol": "SAME", "market": "CN", "current_price": 95, "source": "wrong-market"},
    ])
    view = _bearer(viewer["access_token"])
    write = _bearer(editor["access_token"])
    screened = client.get(f"{MOBILE}/screening?q=TEST&market=US&min_price=10&max_price=20", headers=view)
    assert screened.status_code == 200
    data = screened.json()["data"]
    assert data["missing_quotes"] == 1 and data["currency"] == "USD"
    assert len(data["candidates"]) == 1 and data["candidates"][0]["price"] == 12
    assert data["candidates"][0]["target"]["market"] == "US" and "wrong-market" not in screened.text
    assert client.get(f"{MOBILE}/screening?q=TEST&market=US&min_price=30&max_price=20", headers=view).status_code == 400
    assert client.get(f"{MOBILE}/screening?q=TEST&market=US&min_price=-1", headers=view).status_code == 422
    path = f"{MOBILE}/symbols/watchlist/add"
    body = {"market": "US", "symbol": "SAME"}
    assert client.post(path, headers=view, json=body).status_code == 403
    assert editor["watchlist_manage"] is True
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: client.post(path, headers=write, json=body), range(2)))
    assert [reply.status_code for reply in replies] == [200, 200]
    assert replies[0].json()["data"] == replies[1].json()["data"] == rows[1]
    with sessions() as db:
        assert db.query(Stock).filter_by(symbol="SAME", market="US").count() == 1
    assert client.post("/api/stocks", headers=owner, json={**body, "name": "Duplicate"}).status_code == 400
    assert client.get(f"{MOBILE}/symbols/watchlist", headers=view).json()["data"] == [rows[1]]
