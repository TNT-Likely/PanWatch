from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from src.bootstrap.application import app
from src.modules.administration.api import auth
from src.modules.mobile import api as mobile
from src.modules.portfolio.api import accounts
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.migrations import _m141_mobile_device_pairing
from src.platform.persistence.models import Account, MobileDevice, MobilePairing, Position, Stock

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


def _claim(client, qr, claim_key=PHONE_KEY):
    return client.post(f"{MOBILE}/pairings/{qr['pairing_id']}/claim", json={
        "challenge": qr["challenge"],
        "claim_key": claim_key,
        "device_name": "Test iPhone",
        "platform": "ios",
    })


def _exchange(client, root, claim_key=PHONE_KEY):
    return client.post(f"{root}/exchange", headers=_bearer(claim_key))


def _approve(client, owner, root, qr):
    response = _claim(client, qr)
    assert response.status_code == 200, response.text
    code = response.json()["data"]["verification_code"]
    assert client.get(root, headers=owner).json()["data"]["verification_code"] == code
    response = client.post(f"{root}/decision", headers=owner, json={
        "decision": "approve", "verification_code": code,
    })
    assert response.status_code == 200, response.text


def test_pairing_requires_a_configured_login_and_same_https_origin(instance):
    client, sessions = instance
    assert client.get(f"{MOBILE}/capabilities").json()["data"] == {
        "service": "panwatch", "service_version": mobile.get_app_version(),
        "protocol_min": 1, "protocol_max": 1, "pairing": False, "password_login": False, "portfolio_read": True,
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
    assert _claim(client, qr, "2" * 64).status_code == 409
    assert _exchange(client, root, "2" * 64).status_code == 409
    assert client.get(f"{root}/status", headers=owner).status_code == 404
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: _exchange(client, root), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    grant = next(response.json()["data"] for response in responses if response.status_code == 200)
    assert grant["scope"] == "portfolio:read"
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
    _approve(client, owner, root, qr)
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
    engine.dispose()
