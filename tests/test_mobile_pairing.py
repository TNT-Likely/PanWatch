import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
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
from src.modules.mobile import api as mobile, push, watchlist_overview
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
        "protocol_min": 1, "protocol_max": 1, "pairing": False, "password_login": False, "portfolio_read": True, "assistant_chat": False,
        "session_refresh": True, "watchlist_read": True, "alerts_manage": True,
        "portfolio_metrics": True, "portfolio_contributions": True, "symbol_facts": True, "catalog_symbol_facts": True, "diagnostics_read": True,
        "disclosures": True, "disclosure_archive": True, "fact_tasks": True, "symbol_alert_history": True,
        "screening": True, "screening_metrics": True, "watchlist_manage": True,
        "portfolio_overview": True, "watchlist_overview": True,
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
    assert client.get(f"{MOBILE}/screening?market=US", headers=device).status_code == 403
    assert client.get(f"{MOBILE}/portfolio/contributions", headers=device).json()["data"]["state"] == "empty"
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
    with monkeypatch.context() as context:
        context.setattr(accounts, "daily_quote_fields", lambda *_: {
            "change_pct": None, "daily_move_status": "unavailable", "quote_date": None,
        })
        combined = client.get(f"{MOBILE}/portfolio/overview", headers=device).json()["data"]
    assert {key: combined[key] for key in result} == result
    assert combined["available_funds_cny"] == 50
    assert combined["market_value_cny"] == 20
    assert combined["today_pnl"]["value"] is None
    assert [(row["target"]["market"], row["target"]["symbol"]) for row in combined["contributions"]] == [
        ("CN", "SAME"), ("US", "SAME"),
    ]
    positions_path = f"{MOBILE}/portfolio/positions"
    assert client.get(positions_path, params={"market": "CN", "symbol": "SAME"}).status_code == 401
    china_details = client.get(positions_path, headers=device, params={"market": "CN", "symbol": "SAME"}).json()["data"]
    us_details = client.get(positions_path, headers=device, params={"market": "US", "symbol": "SAME"}).json()["data"]
    assert len(china_details) == len(us_details) == 1
    assert china_details[0]["currency"] == "CNY" and china_details[0]["cost_price"] == 99
    assert china_details[0]["account_name"] == "Private account"
    assert china_details[0]["unrealized_pnl_cny"] == -178
    assert us_details[0]["currency"] == "USD" and us_details[0]["unrealized_pnl_cny"] is None
    assert client.get(positions_path, headers=device, params={"market": "HK", "symbol": "SAME"}).json()["data"] == []
    with sessions() as db:
        position = db.scalar(select(Position).where(Position.stock_id == china_id))
        position.quantity = 3
        position.cost_price = 10
        db.commit()
    assert client.get(f"{MOBILE}/portfolio", headers=device).json()["data"]["total_assets"] == 80
    break_even = client.get(positions_path, headers=device, params={"market": "CN", "symbol": "SAME"}).json()["data"]
    assert break_even[0]["unrealized_pnl_cny"] == 0


def test_watchlist_cards_scope_alert_counts_and_observations_to_each_market(instance, monkeypatch):
    from marketdata.cache import TTLCache
    from marketdata.types import Bar

    client, sessions = instance
    owner = _owner(sessions)

    def pair(*, renewable=False, alerts=False):
        qr = _start(client, owner)
        root = f"{MOBILE}/pairings/{qr['pairing_id']}"
        _approve(client, owner, root, qr, renewable=renewable, manage_alerts=alerts)
        return _bearer(_exchange(client, root).json()["data"]["access_token"])

    with sessions() as db:
        cn = Stock(symbol="SAME", name="China fixture", market="CN", sort_order=1)
        us = Stock(symbol="SAME", name="US fixture", market="US", sort_order=2)
        db.add_all([cn, us])
        db.flush()
        db.add_all([
            PriceAlertRule(stock_id=cn.id, name="CN enabled", enabled=True),
            PriceAlertRule(stock_id=cn.id, name="CN paused", enabled=False),
        ])
        db.commit()
    no_watchlist = pair()
    reader = pair(renewable=True)
    with_alerts = pair(renewable=True, alerts=True)
    path = f"{MOBILE}/symbols/watchlist"
    monkeypatch.setattr(watchlist_overview, "_trend_cache", TTLCache())
    monkeypatch.setattr(watchlist_overview, "md_quote_rows", lambda symbols, market: [
        {"market": "CN", "symbol": "SAME", "current_price": 99.7, "quote_date": "2026-11-20", "source": "isolated"},
    ])

    fetch_calls = []

    def closes(symbol, *, market, days):
        fetch_calls.append((market, symbol, days))
        if market == "US":
            raise RuntimeError("unavailable fixture")
        return [Bar(day, price, price, price, price) for day, price in [
            ("2026-11-20", 100), ("2026-11-19", 99.7), ("invalid", 1),
        ]], "isolated-trend"

    monkeypatch.setattr(watchlist_overview, "get_market_data", lambda: SimpleNamespace(klines_with_vendor=closes))
    assert client.get(f"{path}/overview", headers=no_watchlist).status_code == 403
    assert client.get(f"{path}/trends", headers=no_watchlist).status_code == 403
    assert client.get(f"{path}/overview?limit=7", headers=reader).status_code == 422
    public = client.get(f"{path}/overview?limit=1", headers=reader).json()["data"]
    assert public["total"] == 2 and public["next_offset"] == 1
    assert public["enabled_alerts"] is None and public["items"][0]["enabled_alerts"] is None
    assert public["items"][0]["quote"]["price"] == 99.7
    enriched = client.get(f"{path}/overview", headers=with_alerts).json()["data"]
    assert enriched["enabled_alerts"] == 1 and enriched["next_offset"] is None
    assert [(r["target"]["market"], r["enabled_alerts"]) for r in enriched["items"]] == [("CN", 1), ("US", 0)]
    assert enriched["items"][1]["quote"] is None and enriched["items"][1]["currency"] == "USD"
    trends = client.get(f"{path}/trends", headers=reader).json()["data"]
    assert trends[0]["source"] == "isolated-trend" and trends[0]["points"] == [
        {"date": "2026-11-19", "price": 99.7}, {"date": "2026-11-20", "price": 100},
    ]
    assert trends[1]["source"] is None and trends[1]["points"] == []
    assert client.get(f"{path}/trends?offset=1", headers=reader).json()["data"] == trends[1:]
    assert sorted(fetch_calls) == [("CN", "SAME", 14), ("US", "SAME", 14)]


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


def test_phone_discards_only_its_own_device_session(instance):
    client, sessions = instance
    owner, existing = _renewable_grant(client, sessions)
    _, unsaved = _renewable_grant(client, sessions)
    existing_id, unsaved_id = existing["device_id"], unsaved["device_id"]
    with sessions() as db:
        for device_id in (existing_id, unsaved_id):
            db.add(MobilePushRoute(id=str(uuid4()), device_id=device_id, instance_id=unsaved["instance_id"]))
        db.commit()

    discard = f"{MOBILE}/session"
    assert client.delete(discard, headers=owner).status_code == 401
    assert client.delete(discard, headers=_bearer(unsaved["refresh_token"])).status_code == 401
    phone = _bearer(unsaved["access_token"])
    response = client.delete(discard, headers=phone, params={"device_id": existing_id})
    assert response.status_code == 200 and response.json()["data"] is True
    assert response.headers["Cache-Control"] == "no-store"

    repeated = client.delete(discard, headers=phone)
    assert repeated.status_code == 401 and repeated.json()["error_code"] == "mobile_device_revoked"
    assert client.get(f"{MOBILE}/portfolio", headers=phone).status_code == 401
    assert _renew(client, unsaved["refresh_token"], PHONE_KEY).status_code == 401
    assert client.get(f"{MOBILE}/portfolio", headers=_bearer(existing["access_token"])).status_code == 200
    with sessions() as db:
        assert db.get(MobileDevice, existing_id).revoked_at is None
        assert db.get(MobileDevice, unsaved_id).revoked_at is not None
        routes = {route.device_id: route.retired_at for route in db.scalars(select(MobilePushRoute))}
        assert routes[existing_id] is None
        assert routes[unsaved_id] is not None


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
    with sessions() as db:
        assert db.get(PriceAlertRule, saved["id"]).repeat_mode == "once"
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
    with sessions() as db:
        assert db.get(PriceAlertRule, saved["id"]).enabled is False
    assert asyncio.run(engine.scan_once(only_rule_id=saved["id"]))["triggered"] == 0
    event_id = fired["items"][0]["hit_id"]
    event = client.get(f"{MOBILE}/events/{event_id}", headers=device).json()["data"]
    assert event["id"] == event_id and event["rule_id"] == saved["id"]
    assert event["observed_price"] == 99.70 and event["quote_source"] == "qa_replay"
    assert event["conditions"] == [{"type": "price", "op": "<", "target": 100, "actual": 99.70, "matched": True}]
    assert client.get(f"{MOBILE}/events", headers=device).json()["data"] == [event]
    inbox = client.get(f"{MOBILE}/notifications", headers=device).json()["data"]
    assert [n["target"]["event_id"] for n in inbox["items"]] == [event_id]
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


def test_alert_history_pages_same_second_rows_and_rejects_a_cursor_from_another_scope(instance):
    client, sessions = instance
    _, grant = _renewable_grant(client, sessions, manage_alerts=True)
    _, limited = _renewable_grant(client, sessions)
    reader = _bearer(grant["access_token"])
    route = f"{MOBILE}/alerts/history"
    with sessions() as db:
        china = Stock(symbol="SAME", market="CN", name="CN price")
        us = Stock(symbol="SAME", market="US", name="US price")
        rule = PriceAlertRule(stock=china, name="China price reminder")
        other_rule = PriceAlertRule(stock=us, name="US price reminder")
        hits = [
            PriceAlertHit(stock=china, rule=rule, trigger_bucket=f"china-{index}",
                          trigger_snapshot={"quote": {"current_price": 99.70, "source": "qa-replay"}})
            for index in range(61)
        ]
        other = PriceAlertHit(stock=us, rule=other_rule, trigger_bucket="us")
        db.add_all([*hits, other])
        db.flush()
        ids = [hit.id for hit in hits]
        other_id = other.id
        rule_id, stock_id = rule.id, china.id
        # The legacy scan persists whole-second ISO timestamps directly in SQLite.
        db.execute(text("UPDATE price_alert_hits SET trigger_time = '2026-11-23 01:23:00'"))
        db.commit()

    assert client.get(route, headers=_bearer(limited["access_token"])).status_code == 403
    assert client.get(route, headers=reader, params={"symbol": "SAME"}).status_code == 422
    scope = {"market": "CN", "symbol": "SAME", "rule_id": rule_id}
    page = client.get(route, headers=reader, params=scope).json()["data"]
    assert [hit["id"] for hit in page["items"]] == ids[:-31:-1]
    assert all(hit["observed_price"] == 99.70 and hit["quote_source"] == "qa-replay" for hit in page["items"])
    cursor = page["next_before"]
    assert cursor == ids[-30]

    with sessions() as db:
        db.add(PriceAlertHit(stock_id=stock_id, rule_id=rule_id, trigger_bucket="new",
                             trigger_time=datetime(2026, 11, 23, 1, 24)))
        db.commit()
    next_page = client.get(route, headers=reader, params={**scope, "before": cursor}).json()["data"]
    assert [hit["id"] for hit in next_page["items"]] == ids[-31:0:-1]
    last = client.get(route, headers=reader, params={**scope, "before": next_page["next_before"]}).json()["data"]
    assert [hit["id"] for hit in last["items"]] == [ids[0]]
    assert last["next_before"] is None

    wrong_scope = client.get(route, headers=reader, params={**scope, "before": other_id})
    assert wrong_scope.status_code == 404 and wrong_scope.json()["error_code"] == "mobile_alert_history_cursor_expired"
    assert len(client.get(f"{MOBILE}/events", headers=reader, params=scope).json()["data"]) == 50
    with sessions() as db:
        db.delete(db.get(PriceAlertHit, cursor))
        db.commit()
    removed_anchor = client.get(route, headers=reader, params={**scope, "before": cursor})
    assert removed_anchor.status_code == 404
    assert removed_anchor.json()["error_code"] == "mobile_alert_history_cursor_expired"


def test_mobile_rule_management_keeps_complex_rules_and_reports_scan_health(instance):
    from src.platform.persistence.models import PriceAlertHealth, PriceAlertScanHealth

    client, sessions = instance
    _, grant = _renewable_grant(client, sessions, manage_alerts=True)
    _, preview = _renewable_grant(client, sessions)
    writer = _bearer(grant["access_token"])
    reader = _bearer(preview["access_token"])
    route = f"{MOBILE}/alerts"
    assert client.get(f"{route}/health", headers=reader).status_code == 403
    assert client.get(f"{route}/health", headers=writer).json()["data"]["scan"]["status"] == "never_checked"
    with sessions() as db:
        stock = Stock(symbol="RULE", name="Rule fixture", market="US")
        simple = PriceAlertRule(stock=stock, name="Once price", condition_group={
            "op": "and", "items": [{"type": "price", "op": "<", "value": 100}],
        }, repeat_mode="once", enabled=True)
        complex_rule = PriceAlertRule(stock=stock, name="Compound Web rule", condition_group={
            "op": "or", "items": [{"type": "price", "op": "<", "value": 100}, {"type": "volume", "op": ">", "value": 25}],
        }, enabled=True)
        db.add_all([simple, complex_rule])
        db.flush()
        simple_id, complex_id = simple.id, complex_rule.id
        db.add_all([
            PriceAlertScanHealth(id=1, status="completed", next_scan_at=datetime(2000, 1, 1)),
            PriceAlertHealth(rule_id=simple_id, status="quote_missing", last_checked_at=datetime(2026, 11, 22)),
            PriceAlertHit(stock=stock, rule=simple, trigger_snapshot={}, trigger_bucket="simple"),
            PriceAlertHit(stock=stock, rule=complex_rule, trigger_snapshot={}, trigger_bucket="complex"),
        ])
        db.commit()

    listed = {row["id"]: row for row in client.get(route, headers=writer).json()["data"]}
    assert listed[simple_id]["editable_price"] is True
    assert listed[complex_id]["editable_price"] is False
    health = client.get(f"{route}/health", headers=writer).json()["data"]
    assert health["scan"]["status"] == "monitoring_delayed"
    assert next(row for row in health["items"] if row["rule_id"] == simple_id)["status"] == "quote_missing"

    patch = {"price": 95, "previous_price": 100, "comparison": "<"}
    assert client.put(f"{route}/{simple_id}/price", headers=reader, json=patch).status_code == 403
    assert client.put(f"{route}/{complex_id}/price", headers=writer, json=patch).status_code == 409
    edited = client.put(f"{route}/{simple_id}/price", headers=writer, json=patch)
    assert edited.status_code == 200
    assert edited.json()["data"]["condition_group"]["items"] == [{"type": "price", "op": "<", "value": 95}]
    assert client.put(f"{route}/{simple_id}/price", headers=writer, json=patch).status_code == 409
    with sessions() as db:
        assert db.get(PriceAlertRule, simple_id).repeat_mode == "once"
        assert db.get(PriceAlertRule, complex_id).condition_group == listed[complex_id]["condition_group"]
    assert [item["rule_id"] for item in client.get(f"{MOBILE}/events?rule_id={simple_id}", headers=writer).json()["data"]] == [simple_id]
    assert client.delete(f"{route}/{simple_id}", headers=reader).status_code == 403
    assert client.delete(f"{route}/{simple_id}", headers=writer).json()["data"] is True
    assert client.delete(f"{route}/{simple_id}", headers=writer).json()["data"] is True
    assert client.get(f"{MOBILE}/events?rule_id={simple_id}", headers=writer).json()["data"] == []
    assert len(client.get(f"{MOBILE}/events?rule_id={complex_id}", headers=writer).json()["data"]) == 1


def test_inbox_marks_observed_events_for_one_device_and_preserves_deleted_summary(instance):
    from src.modules.notifications.service import NotificationService
    from src.modules.notifications.sources import price_hit

    client, sessions = instance
    _, first = _renewable_grant(client, sessions, manage_alerts=True)
    _, second = _renewable_grant(client, sessions, manage_alerts=True)
    _, limited = _renewable_grant(client, sessions)
    auth = _bearer(first["access_token"])
    other = _bearer(second["access_token"])
    restricted = _bearer(limited["access_token"])
    url = f"{MOBILE}/notifications"
    unread = f"{url}/unread"
    assert client.get(url).status_code == 401
    assert client.get(unread).status_code == 401
    assert client.get(url, headers=auth).json()["data"]["items"] == []
    assert client.get(unread, headers=auth).json()["data"] is False
    seen_at = datetime(2026, 11, 20, 12, 30)
    with sessions() as db:
        stock = Stock(symbol="ALRT", market="US", name="Inbox fixture")
        rule = PriceAlertRule(stock=stock, name="Saved alert summary")
        hits = [
            PriceAlertHit(stock=stock, rule=rule, trigger_time=seen_at + timedelta(minutes=offset),
                          trigger_bucket=str(offset), trigger_snapshot={})
            for offset in range(2)
        ]
        db.add_all(hits)
        db.flush()
        ids = [price_hit(db, hit, rule).id for hit in hits]
        # These rows use SQLite's historical whole-second timestamp representation.
        db.execute(text("UPDATE notification_events SET occurred_at = '2026-11-20 12:30:00'"))
        deleted_target = hits[-1].id
        rule_id = rule.id
        NotificationService(db).publish(
            source="agent", event_type="agent_succeeded", dedupe_key="legacy-agent-body", subject_kind="agent_run",
            subject_id="12", title="PRIVATE LEGACY REPORT", action={},
        )
        db.commit()

    page = client.get(f"{url}?limit=1", headers=auth).json()["data"]
    newest = page["items"][0]
    assert page["next_before"] == newest["id"] == page["observed_id"] == ids[1]
    assert newest["read_at"] is None and newest["target"]["event_id"] == deleted_target
    follow = f"{url}?limit=1&before={page['next_before']}&through={page['observed_id']}"
    assert client.get(follow, headers=auth).json()["data"]["items"][0]["id"] == ids[0]
    assert client.get(url, headers=restricted).json()["data"]["items"] == []
    assert client.post(f"{url}/read", headers=restricted, json={"ids": ids}).status_code == 200
    assert client.get(unread, headers=restricted).json()["data"] is False
    assert client.post(f"{url}/read", headers=auth, json={"ids": [ids[1]]}).status_code == 200
    assert client.get(unread, headers=auth).json()["data"] is True  # Older page still unread.

    with sessions() as db:
        rule = db.get(PriceAlertRule, rule_id)
        new_hit = PriceAlertHit(stock=rule.stock, rule=rule, trigger_time=seen_at + timedelta(minutes=2),
                               trigger_bucket="2", trigger_snapshot={})
        db.add(new_hit)
        db.flush()
        recent = price_hit(db, new_hit, rule).id
        db.delete(db.get(PriceAlertHit, deleted_target))
        db.commit()

    assert client.post(f"{url}/read", headers=auth, json={"through_id": page["observed_id"]}).status_code == 200
    reply = client.get(url, headers=auth)
    assert "PRIVATE LEGACY REPORT" not in reply.text
    inbox = reply.json()["data"]["items"]
    assert [row["id"] for row in inbox] == [recent, *reversed(ids)]
    assert [row["read_at"] is not None for row in inbox] == [False, True, True]
    assert inbox[1]["target"]["title"] == "Saved alert summary"
    assert client.get(f"{MOBILE}/events/{deleted_target}", headers=auth).status_code == 404
    assert client.get(unread, headers=auth).json()["data"] is True  # Arrived after the bulk-read snapshot.
    assert client.post(f"{url}/read", headers=auth, json={"ids": [recent]}).status_code == 200
    assert client.get(unread, headers=auth).json()["data"] is False
    assert client.get(unread, headers=other).json()["data"] is True
    assert all(row["read_at"] is None for row in client.get(url, headers=other).json()["data"]["items"])
    with sessions() as db:
        assert NotificationService(db).query(source="market", view="unread").count() == 3


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
    assert result.json()["data"]["account_contributions"] is True
    assert result.json()["data"]["accounts"][0]["unrealized_pnl"] == {"value": 3, "coverage": "complete"}
    assert "private_note" not in result.text
    original = client.get(f"{MOBILE}/portfolio", headers=device).json()["data"]
    assert set(original) == {"total_assets", "currency", "valuation", "holdings"}
    assert original["holdings"][0]["currency"] == "USD"
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=owner).status_code == 200
    assert client.get(metrics_path, headers=device).status_code == 401


def test_portfolio_contributions_are_scoped_to_real_holdings_and_quote_coverage(instance, monkeypatch):
    client, sessions = instance
    owner, grant = _renewable_grant(client, sessions)
    assert grant["portfolio_contributions"] is True
    headers = _bearer(grant["access_token"])
    path = f"{MOBILE}/portfolio/contributions"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=owner).status_code == 401
    for params in ({"market": "CN"}, {"symbol": "SAME"}):
        incomplete = client.get(path, params=params, headers=headers)
        assert incomplete.status_code == 400
        assert incomplete.json()["error_code"] == "mobile_contribution_target_incomplete"
    assert client.get(path, params={"market": "XX", "symbol": "SAME"}, headers=headers).status_code == 422

    with sessions() as db:
        first = Account(name="First private account", enabled=True)
        second = Account(name="Second private account", enabled=True)
        disabled = Account(name="Disabled private account", enabled=False)
        china = Stock(symbol="SAME", market="CN", name="China fixture")
        america = Stock(symbol="SAME", market="US", name="US fixture")
        db.add_all([first, second, disabled, china, america, Stock(symbol="UNHELD", market="CN", name="Watchlist only")])
        db.flush()
        first_id, second_id, disabled_id = first.id, second.id, disabled.id
        db.add_all([
            Position(account_id=first.id, stock_id=china.id, quantity=2, cost_price=10),
            Position(account_id=second.id, stock_id=china.id, quantity=4, cost_price=10),
            Position(account_id=second.id, stock_id=america.id, quantity=3, cost_price=10),
            Position(account_id=disabled.id, stock_id=china.id, quantity=10, cost_price=10),
        ])
        db.commit()

    monkeypatch.setattr(accounts, "get_usd_cny_rate", lambda: 7)
    monkeypatch.setattr(trading_calendar, "market_status", lambda *_: "trading")
    quote_time = datetime(2026, 11, 20, 12, tzinfo=UTC)
    monkeypatch.setattr(trading_calendar, "_now_in_market_tz", lambda _: quote_time)
    observations = {"CN": {"symbol": "SAME", "current_price": 12, "prev_close": 10, "quote_date": "2026-11-20"}}
    monkeypatch.setattr(accounts, "md_quote_rows", lambda symbols, market: [observations[market]] if market in observations else [])

    before = datetime.now(UTC)
    response = client.get(path, params={"market": "CN", "symbol": "SAME"}, headers=headers)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    china_data = response.json()["data"]
    stamp = datetime.fromisoformat(china_data.pop("computed_at"))
    assert stamp.utcoffset() == timedelta(0) and before <= stamp <= datetime.now(UTC)
    assert china_data == {
        "phase": "postmarket", "state": "ready", "currency": "CNY", "basis": "current_holdings",
        "account_id": None,
        "total_daily_pnl": {"value": 12, "coverage": "complete"},
        "contributions": [{
            "target": {"symbol": "SAME", "market": "CN", "name": "China fixture"},
            "daily_pnl": {"value": 12, "coverage": "complete"},
            "quote_price": 12, "quote_date": "2026-11-20",
        }],
    }

    missing = client.get(path, params={"market": "US", "symbol": "SAME"}, headers=headers).json()["data"]
    assert missing["state"] == "unavailable"
    assert missing["total_daily_pnl"] == {"value": None, "coverage": "unavailable"}
    assert missing["contributions"] == [{
        "target": {"symbol": "SAME", "market": "US", "name": "US fixture"},
        "daily_pnl": {"value": None, "coverage": "unavailable"},
        "quote_price": None, "quote_date": None,
    }]
    empty = client.get(path, params={"market": "CN", "symbol": "UNHELD"}, headers=headers).json()["data"]
    assert empty["state"] == "empty" and empty["contributions"] == []
    partial = client.get(path, headers=headers).json()["data"]
    assert partial["state"] == "partial"
    assert partial["total_daily_pnl"] == {"value": None, "coverage": "partial"}

    first_scope = client.get(path, headers=headers, params={"account_id": first_id}).json()["data"]
    assert first_scope["account_id"] == first_id and first_scope["state"] == "ready"
    assert first_scope["total_daily_pnl"] == {"value": 4, "coverage": "complete"}
    assert [(row["target"]["market"], row["daily_pnl"]["value"]) for row in first_scope["contributions"]] == [("CN", 4)]
    second_china = client.get(path, headers=headers, params={"account_id": second_id, "market": "CN", "symbol": "SAME"})
    assert second_china.json()["data"]["total_daily_pnl"] == {"value": 8, "coverage": "complete"}
    assert client.get(path, headers=headers, params={"account_id": disabled_id}).status_code == 404
    assert client.get(path, headers=headers, params={"account_id": disabled_id + 99}).status_code == 404
    assert client.get(path, headers=headers, params={"account_id": 0}).status_code == 422

    observations["US"] = {"symbol": "SAME", "current_price": 10, "prev_close": 12}
    whole = client.get(path, headers=headers).json()["data"]
    assert whole["state"] == "ready"
    assert whole["total_daily_pnl"] == {"value": -30, "coverage": "complete"}
    assert {item["target"]["market"]: item["daily_pnl"]["value"] for item in whole["contributions"]} == {"CN": 12, "US": -42}
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=owner).status_code == 200
    assert client.get(path, headers=headers).status_code == 401


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
        source = DataSource(name="Synthetic source", type="quote", provider="fixture", priority=3,
                            config={"secret": sentinel})
        db.add(source)
        db.commit()
        source_id = source.id

    monkeypatch.setattr(marketdata_client, "get_market_data", lambda: SimpleNamespace(health=lambda: {
        "fixture": {"count": 4, "success_rate": 0.75, "last_success_at": 1_700_000_000, "last_error": sentinel},
    }))
    probes = []

    async def probe(source):
        probes.append(source.id)
        return SimpleNamespace(success=len(probes) > 1, count=3, duration_ms=7, error=sentinel)

    monkeypatch.setattr(data_collector, "get_collector_manager", lambda: SimpleNamespace(test_source=probe))
    response = client.get(base, headers=device)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert sentinel not in response.text
    data = response.json()["data"]
    assert data["sources"][0]["provider_success_rate"] == 0.75 and data["sources"][0]["priority"] == 3
    assert data["agents"][0]["label"] == "Synthetic agent" and data["agents"][0]["result"] == "failed"
    path = f"{base}/sources/{source_id}/test"
    assert client.post(path, headers=owner).status_code == 401 and not probes
    tested = client.post(path, headers=device)
    assert tested.status_code == 200 and sentinel not in tested.text
    assert tested.json()["data"]["passed"] is False and tested.json()["data"]["duration_ms"] == 7
    assert tested.json()["data"]["sample_count"] is None
    repeated = client.post(path, headers=device)
    assert repeated.json()["data"]["sample_count"] == 3 and sentinel not in repeated.text
    assert probes == [source_id, source_id]
    rotated = _renew(client, grant["refresh_token"], "e" * 64).json()["data"]
    assert rotated["diagnostics_read"] is True
    assert client.delete(f"{MOBILE}/devices/{grant['device_id']}", headers=owner).status_code == 200
    assert client.post(path, headers=_bearer(rotated["access_token"])).status_code == 401


def test_symbol_facts_keep_market_identity_and_aggregate_real_bar_periods(instance, monkeypatch):
    from datetime import date, timedelta
    from marketdata.types import Bar
    from src.modules.mobile import screening, symbol_facts

    monkeypatch.setattr(screening, "search_stocks", lambda *_args, **_kwargs: [])
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
        assert symbol == "SAME" and days == 360
        price = 100 if market == "US" else 10
        count = 40 if market == "US" else 24
        data = [
            Bar(
                (date(2026, 9, 1) + timedelta(days=index)).isoformat(),
                price, price + index, price + index + 1, price - 1,
                0 if market == "CN" and index == count - 1 else 100 * (index + 1),
            )
            for index in range(count)
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
    assert result["bars_source"] == "us-bars" and len(result["bars"]) == 40
    assert result["bars"][-1]["volume"] == 4000
    assert result["sma20_daily"] == 129.5
    technicals = result["technicals_daily"]
    assert technicals["as_of"] == "2026-10-10"
    assert technicals["macd_hist"] > 0 and technicals["rsi6"] == 100
    assert len(technicals["macd_history"]) == 8
    assert technicals["macd_history"][-1] == technicals["macd_hist"]
    assert technicals["volume_ratio_5d"] == pytest.approx(4000 / 3800)
    us_month = client.get(f"{path}/US/SAME?interval=1m", headers=device).json()["data"]
    assert us_month["bars"][-1]["volume"] == 35500
    assert us_month["technicals_daily"] == technicals
    cn = client.get(f"{path}/CN/SAME?interval=1w", headers=device).json()["data"]
    assert cn["target"]["market"] == "CN" and cn["currency"] == "CNY"
    assert cn["quote"]["price"] == 18 and cn["bars_source"] == "cn-bars"
    assert len(cn["bars"]) == 4 and cn["bars"][-1]["close"] == 33
    assert cn["bars"][-1]["volume"] is None
    assert cn["technicals_daily"]["volume_ratio_5d"] is None
    assert cn["technicals_daily"]["macd_hist"] is None
    assert cn["technicals_daily"]["macd_history"] == []


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
    inbox = f"{MOBILE}/notifications"
    own = client.get(inbox, headers=auth).json()["data"]["items"]
    assert len(own) == 1 and own[0]["target"] == {"kind": "fact_task", "task_id": task_id, "phase": "premarket"}
    assert client.get(inbox, headers=outsider).json()["data"]["items"] == []
    assert client.get(f"{inbox}/unread", headers=auth).json()["data"] is True
    assert client.get(f"{inbox}/unread", headers=outsider).json()["data"] is False
    assert client.post(f"{inbox}/read", headers=outsider, json={"ids": [own[0]["id"]]}).status_code == 200
    assert client.get(inbox, headers=auth).json()["data"]["items"][0]["read_at"] is None
    assert client.post(f"{inbox}/read", headers=auth, json={"ids": [own[0]["id"]]}).status_code == 200
    assert client.get(inbox, headers=auth).json()["data"]["items"][0]["read_at"] is not None
    assert client.get(f"{inbox}/unread", headers=auth).json()["data"] is False
    assert client.delete(f"{MOBILE}/devices/{device['device_id']}", headers=owner).status_code == 200
    assert client.get(route, headers=auth).status_code == 401
    assert client.get(inbox, headers=auth).status_code == 401


def test_fact_history_range_paginates_with_device_bound_cursors(instance):
    from src.platform.persistence.models import MobileFactTask

    client, sessions = instance
    _, device = _renewable_grant(client, sessions)
    _, other = _renewable_grant(client, sessions)
    auth = _bearer(device["access_token"])
    start, end = date(2026, 11, 20), date(2026, 11, 21)

    def row(day, *, owner=device["device_id"], phase="intraday", market="CN"):
        return MobileFactTask(
            id=str(uuid4()), device_id=owner, phase=phase, market=market, symbol="SAME", state="complete",
            created_on=day, created_at=int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp()),
        )

    matching = [row(start), *(row(end) for _ in range(34))]
    foreign = row(end, owner=other["device_id"])
    wrong_market = row(end, market="US")
    with sessions() as db:
        db.add_all([*matching, foreign, wrong_market, row(end, phase="postmarket"),
                    row(start - timedelta(days=1)), row(end + timedelta(days=1))])
        expected = [item.id for item in sorted(matching, key=lambda item: (item.created_at, item.id), reverse=True)]
        foreign_id, wrong_market_id = foreign.id, wrong_market.id
        db.commit()

    route = f"{MOBILE}/facts/task-history"
    filters = {"start_on": str(start), "end_on": str(end), "market": "CN", "symbol": "SAME", "phase": "intraday"}
    first = client.get(route, params=filters, headers=auth)
    assert first.status_code == 200
    page = first.json()["data"]
    assert [item["id"] for item in page["items"]] == expected[:30]
    assert page["next_before"] == expected[29] and all("result" not in item for item in page["items"])
    with sessions() as db:
        newest = row(end)
        newest.created_at += 1
        db.add(newest)
        db.commit()
    rest = client.get(route, params={**filters, "before": page["next_before"]}, headers=auth)
    assert rest.status_code == 200
    assert [item["id"] for item in rest.json()["data"]["items"]] == expected[30:]
    assert rest.json()["data"]["items"][-1]["created_on"] == str(start)
    assert rest.json()["data"]["next_before"] is None

    legacy = client.get(f"{MOBILE}/facts/tasks", params={"on": str(start), "market": "CN", "symbol": "SAME"}, headers=auth)
    assert [item["id"] for item in legacy.json()["data"]] == [expected[-1]]
    assert client.get(route, params=filters).status_code == 401
    assert client.get(route, params={**filters, "start_on": "2026-11-22"}, headers=auth).status_code == 422
    assert client.get(route, params={"market": "CN"}, headers=auth).status_code == 422
    for cursor in (foreign_id, wrong_market_id):
        invalid = client.get(route, params={**filters, "before": cursor}, headers=auth)
        assert invalid.status_code == 422
        assert invalid.json()["error_code"] == "mobile_fact_history_cursor_invalid"


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
    snapshot = fact_snapshots.build_postmarket_snapshot({"accounts": [{"positions": [
        {"market": "CN", "symbol": "SAME", "name": "CN quote", "daily_pnl": 8.0},
        {"market": "US", "symbol": "SAME", "name": "US quote", "daily_pnl": None},
    ]}], "total": {"total_daily_pnl": 8.0}}, None)
    assert snapshot.state == "partial"
    assert snapshot.total_daily_pnl.value is None
    assert [item.daily_pnl.value for item in snapshot.contributions] == [8.0, None]


def test_screening_is_numeric_and_watchlist_add_is_explicit_and_idempotent(instance, monkeypatch):
    from marketdata.types import Bar
    from src.modules.mobile import screening, symbol_facts

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
    quotes = [
        {"symbol": "EXP", "market": "US", "current_price": 50},
        {"symbol": "SAME", "market": "US", "current_price": 12, "source": "fixture-quote", "quote_date": "2026-11-20"},
        {"symbol": "SAME", "market": "CN", "current_price": 95, "source": "wrong-market"},
    ]
    monkeypatch.setattr(screening, "md_quote_rows", lambda *_: quotes)
    monkeypatch.setattr(symbol_facts, "md_quote_rows", lambda *_: quotes)

    def bars(symbol, *, market, days):
        assert (market, symbol) == ("US", "SAME")
        return [Bar("2026-11-19", 10, 11, 12, 9, 50), Bar("2026-11-20", 11, 12, 13, 10, 60)], "fixture-bars"

    monkeypatch.setattr(symbol_facts, "get_market_data", lambda: SimpleNamespace(klines_with_vendor=bars))
    view = _bearer(viewer["access_token"])
    write = _bearer(editor["access_token"])
    default = client.get(f"{MOBILE}/screening?q=TEST&market=US", headers=view).json()["data"]
    assert (default["sort"], default["direction"]) == ("symbol", "asc")
    assert [row["target"]["symbol"] for row in default["candidates"]] == ["EXP", "NODATA", "SAME"]
    screened = client.get(f"{MOBILE}/screening?q=TEST&market=US&min_price=10&max_price=20", headers=view)
    assert screened.status_code == 200
    data = screened.json()["data"]
    assert data["missing_quotes"] == 1 and data["currency"] == "USD"
    assert len(data["candidates"]) == 1 and data["candidates"][0]["price"] == 12
    assert data["candidates"][0]["target"]["market"] == "US" and "wrong-market" not in screened.text
    assert client.get(f"{MOBILE}/screening?q=TEST&market=US&min_price=30&max_price=20", headers=view).status_code == 400
    assert client.get(f"{MOBILE}/screening?q=TEST&market=US&min_price=-1", headers=view).status_code == 422
    assert viewer["catalog_symbol_facts"] and not viewer["watchlist_manage"]
    symbol_path = f"{MOBILE}/symbols"
    facts = client.get(f"{symbol_path}/US/SAME", headers=view).json()["data"]
    assert facts["target"] == rows[1] and facts["currency"] == "USD"
    assert (facts["quote"]["price"], facts["quote"]["source"]) == (12, "fixture-quote")
    assert (facts["bars_source"], [bar["close"] for bar in facts["bars"]]) == ("fixture-bars", [11, 12])
    assert client.get(f"{symbol_path}/HK/SAME", headers=view).status_code == 404
    assert client.get(f"{symbol_path}/US/SAM", headers=view).status_code == 404
    assert client.get(f"{symbol_path}/watchlist", headers=view).json()["data"] == []
    with sessions() as db:
        assert db.query(Stock).count() == db.query(PriceAlertRule).count() == 0
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


def test_watchlist_removal_is_market_scoped_and_preserves_dependencies(instance):
    from src.platform.persistence.models import StockAgent

    client, sessions = instance
    _, viewer = _renewable_grant(client, sessions)
    _, editor = _renewable_grant(client, sessions, manage_watchlist=True)
    read = _bearer(viewer["access_token"])
    write = _bearer(editor["access_token"])
    with sessions() as db:
        protected = Stock(symbol="SAME", name="Protected China", market="CN")
        duplicate = Stock(symbol="SAME", name="Old China duplicate", market="CN")
        removable = Stock(symbol="SAME", name="Removable US", market="US")
        legacy = Stock(symbol="LEGACY", name="Legacy holding", market="CN", quantity=1)
        db.add_all([protected, duplicate, removable, legacy])
        db.flush()
        db.add(StockAgent(stock_id=duplicate.id, agent_name="fixture_agent"))
        db.commit()

    path = f"{MOBILE}/symbols/watchlist/remove"
    cn = {"market": "CN", "symbol": "SAME"}
    us = {"market": "US", "symbol": "SAME"}
    assert client.post(path, headers=read, json=us).status_code == 403
    blocked = client.post(path, headers=write, json=cn)
    assert blocked.status_code == 409 and blocked.json()["error_code"] == "mobile_watchlist_in_use"
    assert client.post(path, headers=write, json={"market": "CN", "symbol": "LEGACY"}).status_code == 409
    for _ in range(2):
        assert client.post(path, headers=write, json=us).json()["data"] is True
    with sessions() as db:
        assert db.query(Stock).filter_by(symbol="SAME", market="US").count() == 0
        assert db.query(Stock).filter_by(symbol="SAME", market="CN").count() == 2
        assert db.query(StockAgent).filter_by(agent_name="fixture_agent").count() == 1


def test_screening_ranks_actual_observations_within_the_selected_watchlist_market(instance, monkeypatch):
    from src.modules.mobile import screening

    client, sessions = instance
    _, viewer = _renewable_grant(client, sessions)
    assert viewer["screening_metrics"] is True
    headers = _bearer(viewer["access_token"])
    symbols = {"BETA", "ALPHA", "LOSS", "ZERO", "MISSING", "INVALID"}
    with sessions() as db:
        db.add_all([Stock(symbol=symbol, market="US", name=symbol) for symbol in sorted(symbols, reverse=True)])
        db.add(Stock(symbol="BETA", market="CN", name="Other market"))
        db.commit()

    def unexpected_search(*_args, **_kwargs):
        pytest.fail("An empty search must never query the stock catalog")

    def quotes(symbols_requested, market):
        assert set(symbols_requested) == symbols
        assert market == "US"
        return [
            {"symbol": symbol, "market": market, "current_price": 12, "source": "fixture", "quote_date": "2026-11-20",
             "change_pct": change, "turnover": turnover, "volume_ratio": ratio}
            for symbol, change, turnover, ratio in [
                ("BETA", 2, 200, 0.5), ("ALPHA", 2, 250, 1),
                ("LOSS", -1.25, 130, 3), ("ZERO", 0, 0, 0),
                ("INVALID", float("nan"), -1, -1),
            ]
        ] + [{"symbol": "BETA", "market": "CN", "current_price": 1, "change_pct": 90, "source": "wrong-market"}]

    monkeypatch.setattr(screening, "search_stocks", unexpected_search)
    monkeypatch.setattr(screening, "md_quote_rows", quotes)
    path = f"{MOBILE}/screening"
    assert client.get(path, params={"market": "US"}).status_code == 401

    for sort, direction, expected in [
        ("change_pct", "desc", ["ALPHA", "BETA", "ZERO", "LOSS", "INVALID", "MISSING"]),
        ("change_pct", "asc", ["LOSS", "ZERO", "ALPHA", "BETA", "INVALID", "MISSING"]),
        ("turnover", "desc", ["ALPHA", "BETA", "LOSS", "ZERO", "INVALID", "MISSING"]),
        ("volume_ratio", "asc", ["ZERO", "BETA", "ALPHA", "LOSS", "INVALID", "MISSING"]),
        ("symbol", "desc", ["ZERO", "MISSING", "LOSS", "INVALID", "BETA", "ALPHA"]),
    ]:
        reply = client.get(path, params={"market": "US", "q": "   ", "sort": sort, "direction": direction}, headers=headers)
        assert reply.status_code == 200
        data = reply.json()["data"]
        assert (data["sort"], data["direction"], data["currency"], data["missing_quotes"]) == (sort, direction, "USD", 1)
        rows = data["candidates"]
        assert [row["target"]["symbol"] for row in rows] == expected
        assert all(row["target"]["market"] == "US" and row["saved"] for row in rows)
        beta = next(row for row in rows if row["target"]["symbol"] == "BETA")
        assert (beta["change_pct"], beta["turnover"], beta["volume_ratio"], beta["source"], beta["quote_date"]) == (
            2, 200, 0.5, "fixture", "2026-11-20",
        )
        zero = next(row for row in rows if row["target"]["symbol"] == "ZERO")
        assert (zero["change_pct"], zero["turnover"], zero["volume_ratio"]) == (0, 0, 0)
        for row in rows:
            if row["target"]["symbol"] in ("MISSING", "INVALID"):
                assert row["change_pct"] is None and row["turnover"] is None and row["volume_ratio"] is None

    empty = client.get(path, params={"market": "HK", "sort": "turnover", "direction": "desc"}, headers=headers)
    assert empty.status_code == 200
    assert empty.json()["data"] == {
        "candidates": [], "currency": "HKD", "missing_quotes": 0, "sort": "turnover", "direction": "desc",
    }
    for invalid in ({"sort": "prediction"}, {"direction": "other"}, {"q": "x" * 49}):
        assert client.get(path, params={"market": "US", **invalid}, headers=headers).status_code == 422


def test_mobile_chat_stays_device_owned_and_renders_only_selected_facts(instance, monkeypatch):
    from src.modules.mobile import assistant
    from src.platform.persistence.models import AIModel, AIService

    client, sessions = instance
    uri = f"{MOBILE}/assistant/conversations"
    _, first = _renewable_grant(client, sessions)
    header = _bearer(first["access_token"])
    assert first["assistant_chat"] is False
    assert client.get(uri, headers=header).status_code == 503
    with sessions() as db:
        service = AIService(name="Unit test model", base_url="http://127.0.0.1:1/v1")
        db.add(service)
        db.flush()
        model = AIModel(service_id=service.id, name="Test", model="unit-fixture")
        db.add(model)
        db.commit()
        monkeypatch.setenv("MOBILE_ASSISTANT_MODEL_ID", str(model.id))

    class TestModel:
        invocations = 0

        def __init__(self, *args):
            self.client = self

        async def close(self):
            pass

        async def chat_stream(self, messages, **kwargs):
            TestModel.invocations += 1
            assert not kwargs.get("tools")
            yield "token", "F0"
            yield "token", "1\n"
            yield "token", "Content that is not in the source."
            yield "message", {"content": "", "tool_calls": []}

    monkeypatch.setattr(assistant, "AIClient", TestModel)
    assert client.get(f"{MOBILE}/capabilities").json()["data"]["assistant_chat"]
    _, second = _renewable_grant(client, sessions)
    assert second["assistant_chat"]
    other = _bearer(second["access_token"])
    thread = uri + "/" + str(uuid4())
    turn = thread + "/turns/" + str(uuid4())
    body = {"text": "RSI 的范围是什么？", "context": None, "locale": "zh-CN"}
    assert client.put(turn, headers=header, json=body).status_code == 200
    result = client.get(thread, headers=header).json()["data"]
    assert result["turns"][0]["state"] == "complete"
    assert "0 至 100" in result["turns"][0]["answer"]
    assert "not in the source" not in result["turns"][0]["answer"]
    assert len(result["turns"][0]["sources"]) == 1
    assert client.put(turn, headers=header, json=body).json()["data"] == result
    assert TestModel.invocations == 1
    assert client.put(turn, headers=header, json={**body, "text": "different"}).status_code == 409
    assert client.get(thread, headers=other).status_code == 404
    assert client.get(uri, headers=other).json()["data"]["items"] == []
    assert client.post(turn + "/cancel", headers=other).status_code == 404
    assert client.get(uri, headers=header).json()["data"]["items"][0]["id"] == result["id"]
    assert client.get("/api/assistant/conversations", headers=header).status_code in (401, 403)


def test_chat_only_selects_evidence_for_the_asked_subject_and_date():
    from datetime import date

    from src.modules.mobile.assistant import _relevant
    from src.modules.mobile.assistant_facts import Evidence

    day = date(2026, 11, 23)
    quote = Evidence(kind="quote", title="云帆 · CN 600901", detail="38.62", observed_on=day)
    latest_close = Evidence(kind="close", title=quote.title, detail="39.52", observed_on=day)
    older_close = Evidence(kind="close", title=quote.title, detail="34.17", observed_on=date(2026, 10, 19))
    definition = Evidence(kind="definition", title="RSI · 6", detail="0 至 100", metric="rsi")
    data = [quote, latest_close, older_close, definition]
    assert _relevant("苏州的天气怎么样？", data) == []
    assert _relevant("1998-05-01 的收盘价是多少？", data) == []
    assert _relevant("2026年10月19日的收盘价是多少？", data) == [older_close]
    assert _relevant("这份简报里云帆的报价是多少？", data) == [quote]
    assert _relevant("这只标的最新价格是多少？", data) == [quote]
    assert _relevant("1998-05-01 的价格是多少？", data) == []
    assert _relevant("CN 900001 的报价是多少？", data) == []
    basis = Evidence(kind="basis", title="Valuation sources", detail="Test basis")
    coverage = Evidence(kind="coverage", title="Valuation coverage", detail="Test coverage")
    lookback = Evidence(kind="lookback", title="Fixed-holdings lookback", detail="Synthetic closing value",
                        observed_on=day, sensitive_amounts=True)
    for prompt in ("这份资料的主要依据是什么？", "What evidence supports this?"):
        assert _relevant(prompt, data + [basis, coverage]) == [basis]
    for prompt in ("还有哪些数据缺失？", "What information is missing?"):
        assert _relevant(prompt, data + [basis, coverage]) == [coverage]
    assert _relevant("2026-11-23 按当前持仓回看", data + [lookback]) == [lookback]
    assert _relevant("1998-05-01 按当前持仓回看", data + [lookback]) == []
    assert _relevant("1998-05-01 实际账户收益是多少？", data + [lookback]) == []
    assert _relevant("What does RSI mean?", data) == [definition]
    assert _relevant("当前 RSI6 读数是多少？", data) == []
    assert _relevant("它的范围呢？", data, "RSI6 怎么解释？") == [definition]


def test_mobile_chat_requires_one_bound_explicit_decision_for_each_price_alert(instance, monkeypatch):
    from src.modules.mobile import assistant
    from src.platform.persistence.models import AIModel, AIService, MobileAssistantApproval

    client, sessions = instance
    with sessions() as db:
        service = AIService(name="Unit model", base_url="http://127.0.0.1:1/v1")
        db.add(service)
        db.flush()
        model = AIModel(service_id=service.id, name="Unit", model="local")
        db.add_all([model, Stock(market="CN", symbol="600901", name="云帆科技")])
        db.commit()
        monkeypatch.setenv("MOBILE_ASSISTANT_MODEL_ID", str(model.id))
    _, grant = _renewable_grant(client, sessions, manage_alerts=True)
    _, outsider = _renewable_grant(client, sessions, manage_alerts=True)
    _, reader = _renewable_grant(client, sessions)
    def new_request(header, question, *, context=None):
        thread = f"{MOBILE}/assistant/conversations/{uuid4()}"
        turn = thread + f"/turns/{uuid4()}"
        body = {"text": question, "context": context, "locale": "zh-CN"}
        result = client.put(turn, headers=header, json=body)
        assert result.status_code == 200, result.text
        reply = result.json()["data"]["turns"][0]
        assert reply["state"] == "complete"
        return turn, reply["approval"], body

    headers = _bearer(grant["access_token"])
    q = "当 CN 600901 价格低于 35 时提醒我"
    turn, approval, body = new_request(headers, q)
    assert approval["state"] == "pending" and approval["rule_id"] is None
    assert (approval["symbol"], approval["comparison"], approval["price"], approval["currency"]) == ("600901", "<", 35, "CNY")
    with sessions() as db:
        assert db.query(PriceAlertRule).count() == 0
    assert client.put(turn, headers=headers, json=body).json()["data"]["turns"][0]["approval"] == approval
    decision = turn + "/approvals/" + approval["id"]
    assert client.post(decision, headers=_bearer(outsider["access_token"]), json={"decision": "approve"}).status_code == 404
    assert client.post(decision, headers=headers, json={"decision": "approve", "price": 7}).status_code == 422
    for _ in range(2):
        result = client.post(decision, headers=headers, json={"decision": "approve"})
        assert result.status_code == 200, result.text
        stored = result.json()["data"]["turns"][0]["approval"]
        assert stored["state"] == "approved" and stored["rule_id"]
    assert client.post(decision, headers=headers, json={"decision": "reject"}).status_code == 409
    rejected_turn, rejected, _ = new_request(headers, "价格高于 40 时通知我", context={"kind":"symbol","market":"CN","symbol":"600901"})
    res = client.post(rejected_turn + "/approvals/" + rejected["id"], headers=headers, json={"decision":"reject"})
    assert res.json()["data"]["turns"][0]["approval"]["state"] == "rejected"
    expired_turn, pending, _ = new_request(headers, q)
    with sessions() as db:
        db.get(MobileAssistantApproval, pending["id"]).expires_at = assistant._now() - 1
        db.commit()
    expired = client.post(expired_turn + "/approvals/" + pending["id"], headers=headers, json={"decision":"approve"})
    assert expired.json()["data"]["turns"][0]["approval"]["state"] == "expired"
    assert new_request(headers, "当 CN 600901 价格低于 35 或高于 39 时提醒我")[1] is None
    assert new_request(_bearer(reader["access_token"]), q)[1] is None
    with sessions() as db:
        rule = db.query(PriceAlertRule).one()
        assert rule.id == stored["rule_id"] and rule.repeat_mode == "once"
        assert rule.condition_group == {"op":"and", "items":[{"type":"price", "op":"<", "value":35}]}
