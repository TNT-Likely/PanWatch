"""Follow the production MCP discovery challenge through each proxy mode."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.bootstrap.application import oauth_protected_resource_metadata
from src.modules.administration.api import mcp, pats
from src.modules.administration.pat import hash_token
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import PersonalAccessToken
from src.web.public_path import PublicPathMiddleware
from src.web.response import ResponseWrapperMiddleware


ORIGIN = "https://panwatch.example:8443"
METADATA_PATH = "/.well-known/oauth-protected-resource"
RPC = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
UNTRUSTED_HEADERS = {
    "Forwarded": 'host="evil.example";proto=http',
    "X-Forwarded-Host": "evil.example",
    "X-Forwarded-Proto": "http",
    "X-Forwarded-Prefix": "/evil",
    "X-Script-Name": "/evil",
}


@pytest.fixture(params=[
    ("", "preserve"), ("", "strip"),
    ("/panwatch", "preserve"), ("/panwatch", "strip"),
    ("/apps/panwatch", "preserve"), ("/apps/panwatch", "strip"),
])
def deployment(request):
    prefix, mode = request.param
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)

    def database():
        with session() as db:
            yield db

    app = FastAPI(redirect_slashes=False)
    app.add_middleware(ResponseWrapperMiddleware)
    app.add_middleware(PublicPathMiddleware, base_path=prefix, proxy_mode=mode)
    app.include_router(mcp.router, prefix="/mcp")
    app.include_router(pats.router, prefix="/api/pats")
    # Exercise the production metadata handler, not a test implementation.
    app.add_api_route(METADATA_PATH, oauth_protected_resource_metadata, methods=["GET"])
    app.dependency_overrides[get_db] = database

    with TestClient(app, base_url=ORIGIN) as client:
        def upstream(public_path):
            assert public_path.startswith(prefix + "/")
            return public_path if mode == "preserve" else public_path[len(prefix):]

        yield client, prefix, mode, upstream, session
    engine.dispose()


def assert_discovery(response, deployment):
    client, prefix, mode, upstream, _ = deployment
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge == f'Bearer resource_metadata="{ORIGIN}{prefix}{METADATA_PATH}"'
    metadata_url = challenge.split('"')[1]
    url = urlsplit(metadata_url)
    # Follow the URL advertised to the client through the configured proxy.
    metadata = client.get(upstream(url.path), headers=UNTRUSTED_HEADERS)
    assert metadata.status_code == 200
    assert metadata.json() == {
        "resource": ORIGIN + prefix + "/mcp",
        "authorization_servers": [],
        "bearer_methods_supported": ["header"],
    }
    if prefix and mode == "preserve":
        # Discovery must not require exposing a root-level route outside the app.
        assert client.get(METADATA_PATH).status_code == 404


def create_pat(deployment):
    client, prefix, _, upstream, _ = deployment
    response = client.post(upstream(prefix + "/api/pats"), json={"name": "discovery-test"})
    assert response.status_code == 200
    return response.json()["data"]["token"]


@pytest.mark.parametrize("suffix", ["", "/"])
def test_missing_pat_advertises_discoverable_metadata(deployment, suffix):
    client, prefix, _, upstream, _ = deployment
    response = client.post(upstream(prefix + "/mcp" + suffix), json=RPC, headers=UNTRUSTED_HEADERS)
    assert response.json()["detail"]["code"] == "mcp_bearer_required"
    assert_discovery(response, deployment)


@pytest.mark.parametrize("state", ["invalid", "revoked", "expired"])
def test_rejected_pat_keeps_discovery_challenge(deployment, state):
    client, prefix, _, upstream, session = deployment
    token = "pwmcp_notarealtoken" if state == "invalid" else create_pat(deployment)
    if state != "invalid":
        with session() as db:
            row = db.query(PersonalAccessToken).filter_by(token_hash=hash_token(token)).one()
            past = datetime.now(timezone.utc) - timedelta(days=1)
            if state == "revoked":
                row.revoked_at = past
            else:
                row.expires_at = past
            db.commit()
    response = client.post(
        upstream(prefix + "/mcp"), json=RPC,
        headers={**UNTRUSTED_HEADERS, "Authorization": f"Bearer {token}"},
    )
    assert response.json()["detail"]["code"] == f"mcp_token_{state}"
    assert_discovery(response, deployment)


def test_discovery_does_not_change_pat_authentication(deployment):
    client, prefix, _, upstream, _ = deployment
    path = upstream(prefix + "/mcp")
    jwt = client.post(path, json=RPC, headers={"Authorization": "Bearer fake.jwt.token"})
    assert jwt.status_code == 403
    assert jwt.json()["detail"]["code"] == "mcp_pat_required"
    token = create_pat(deployment)
    accepted = client.post(path, json=RPC, headers={"Authorization": f"Bearer {token}"})
    assert accepted.status_code == 200
    assert accepted.json()["jsonrpc"] == "2.0"
    assert accepted.json()["result"]["serverInfo"]["name"] == "PanWatch"
