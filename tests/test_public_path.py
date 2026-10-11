"""Regression contract for one runtime-configured public prefix and proxy mode."""

import asyncio

import pytest
from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.modules.administration.api import auth
from src.platform.persistence.database import get_db
from src.platform.runtime.config import Settings
from src.web.public_path import PublicPathMiddleware, app_path, normalize_base_path, public_path
from src.web.response import ResponseWrapperMiddleware
from src.web.spa import install_spa, render_spa_html


@pytest.mark.parametrize("value,expected", [("", ""), ("/", ""), ("/panwatch", "/panwatch"), ("/apps/panwatch/", "/apps/panwatch"), ("/app.v2/foo-bar_1~", "/app.v2/foo-bar_1~")])
def test_normalize_base_path(value, expected):
    assert normalize_base_path(value) == expected
    assert Settings(_env_file=None, panwatch_base_path=value).panwatch_base_path == expected


@pytest.mark.parametrize("value", ["panwatch", "//evil", "/a//b", "/a//", "/a/../b", "/a/./b", "/a/%2f/b", "/a%5Cb", "/a\\b", "/a?b", "/a#b", "/a b", "/a\n", "https://evil/a", '/x\"><script>', "/中文"])
def test_invalid_base_path_fails_closed(value):
    with pytest.raises(ValueError):
        normalize_base_path(value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, panwatch_base_path=value)


def test_explicit_configuration(monkeypatch):
    monkeypatch.setenv("PANWATCH_BASE_PATH", "/apps/panwatch/")
    monkeypatch.setenv("PANWATCH_PROXY_MODE", "strip")
    settings = Settings(_env_file=None)
    assert settings.panwatch_proxy_mode == "strip"
    assert public_path("/api/auth/login") == "/apps/panwatch/api/auth/login"
    assert public_path("/", "") == "/"
    for invalid in ("https://evil", "//evil", "/\\evil"):
        with pytest.raises(ValueError):
            public_path(invalid, "/app")
    monkeypatch.setenv("PANWATCH_PROXY_MODE", "auto")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.fixture(params=[("", "preserve"), ("/panwatch", "preserve"), ("/apps/panwatch", "preserve"), ("/panwatch", "strip"), ("/apps/panwatch", "strip")])
def deployment(request, tmp_path, monkeypatch):
    prefix, mode = request.param
    app = FastAPI(redirect_slashes=False)
    app.add_middleware(ResponseWrapperMiddleware)
    app.add_middleware(PublicPathMiddleware, base_path=prefix, proxy_mode=mode)
    app.include_router(auth.router, prefix="/api/auth")
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(auth, "get_password_hash", lambda db: auth.hash_password("test-password"))
    monkeypatch.setattr(auth, "get_stored_username", lambda db: "test-user")
    monkeypatch.setattr(auth, "get_jwt_secret", lambda: "unit-test-only-signing-secret-32-characters")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/private", dependencies=[Depends(auth.get_current_user)])
    def private():
        return {"authenticated": True}

    @app.get("/api/events", dependencies=[Depends(auth.get_current_user)])
    async def events(request: Request):
        async def stream():
            yield f'id: {int(request.headers.get("last-event-id", "0")) + 1}\nevent: update\ndata: {{"ok":true}}\n\n'
            yield 'event: done\ndata: {}\n\n'
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/.well-known/oauth-protected-resource")
    def metadata(request: Request):
        return {"resource": f'{str(request.base_url).rstrip("/")}/mcp'}

    @app.websocket("/socket")
    async def socket(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text(websocket.scope["root_path"])
        await websocket.close()

    (tmp_path / "index.html").write_text('<html><head><base href="/" data-panwatch-base><meta name="panwatch-base-path" content=""></head><body><script src="./assets/app.js"></script></body></html>')
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app.js").write_text("console.log('loaded')")
    (tmp_path / "sw.js").write_text("// service worker")
    (tmp_path / "escape.txt").symlink_to(tmp_path.parent / "secret.txt")
    (tmp_path.parent / "secret.txt").write_text("must not be served")
    install_spa(app, tmp_path, prefix)
    client = TestClient(app, base_url="http://127.0.0.1:8080")
    upstream = lambda path: prefix + path if mode == "preserve" else path
    return client, prefix, mode, upstream


def test_root_login_deeplinks_assets_and_api(deployment):
    client, prefix, mode, upstream = deployment
    for path in ("/", "/login", "/assistant/123", "/portfolio?account=1", "/index.html"):
        response = client.get(upstream(path))
        assert response.status_code == 200
        assert f'<base href="{prefix}/"' in response.text
        assert f'name="panwatch-base-path" content="{prefix}"' in response.text
        assert response.headers["cache-control"] == "no-cache"
    assert client.get(upstream("/assets/app.js")).text == "console.log('loaded')"
    assert client.get(upstream("/sw.js")).headers["cache-control"] == "no-cache"
    assert client.get(upstream("/api/health")).json()["data"] == {"status": "ok"}
    for path in ("/assets/missing.js", "/api/missing", "/mcp/missing", "/escape.txt", "/missing.png"):
        assert client.get(upstream(path)).status_code == 404
    if prefix and mode == "preserve":
        for path in ("/", "/login", "/api/health", "/assets/app.js", prefix + "-other/api/health"):
            assert client.get(path).status_code == 404
        redirect = client.get(prefix + "?x=1", follow_redirects=False)
        assert redirect.status_code == 308
        assert redirect.headers["location"] == prefix + "/?x=1"


def test_auth_and_realtime_keep_prefix_and_envelope(deployment):
    client, prefix, mode, upstream = deployment
    assert client.get(upstream("/api/auth/status")).json()["data"]["initialized"] is True
    assert client.get(upstream("/api/private")).status_code == 401
    bad = client.post(upstream("/api/auth/login"), json={"username": "test-user", "password": "wrong"})
    assert bad.status_code == 401 and bad.json()["code"] == 401
    response = client.post(upstream("/api/auth/login"), json={"username": "test-user", "password": "test-password"})
    token = response.json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}", "Last-Event-ID": "5"}
    assert client.get(upstream("/api/private"), headers=headers).json()["data"]["authenticated"]
    events = client.get(upstream("/api/events"), headers=headers)
    assert events.headers["content-type"].startswith("text/event-stream")
    assert events.text.startswith('id: 6\nevent: update\ndata: {"ok":true}\n\n')
    assert 'event: done' in events.text
    with client.websocket_connect(upstream("/socket")) as websocket:
        assert websocket.receive_text() == prefix


def test_docs_metadata_and_forwarded_prefix(deployment):
    client, prefix, mode, upstream = deployment
    headers = {"X-Forwarded-Prefix": "/evil", "X-Script-Name": "/evil"}
    response = client.get(upstream("/.well-known/oauth-protected-resource"), headers=headers)
    assert response.json()["resource"] == "http://127.0.0.1:8080" + prefix + "/mcp"
    docs = client.get(upstream("/docs"), headers=headers)
    assert f"url: '{prefix}/openapi.json'" in docs.text
    schema = client.get(upstream("/openapi.json"), headers=headers).json()
    assert "/api/health" in schema["paths"]
    if prefix:
        assert schema["servers"][0]["url"] == prefix


def test_sse_is_forwarded_before_stream_finishes():
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        messages = []
        async def stream(scope, receive, send):
            assert scope["root_path"] == "/nested/app"
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
            await send({"type": "http.response.body", "body": b"data: first\n\n", "more_body": True})
            entered.set()
            await release.wait()
            await send({"type": "http.response.body", "body": b"data: last\n\n", "more_body": False})
        async def send(message):
            messages.append(message)
        async def receive():
            return {"type": "http.disconnect"}
        middleware = PublicPathMiddleware(ResponseWrapperMiddleware(stream), "/nested/app", "strip")
        task = asyncio.create_task(middleware({"type": "http", "path": "/api/events"}, receive, send))
        await asyncio.wait_for(entered.wait(), 1)
        assert messages[-1]["body"] == b"data: first\n\n"
        assert not task.done()
        release.set()
        await task
    asyncio.run(run())


def test_spa_requires_matching_build():
    with pytest.raises(RuntimeError, match="rebuild frontend"):
        render_spa_html("<html>old frontend</html>", "/app")


@pytest.mark.parametrize("prefix", ["", "/panwatch", "/apps/panwatch"])
def test_advertised_task_links_use_public_prefix(prefix, monkeypatch):
    from types import SimpleNamespace
    from src.modules.assistant import api
    monkeypatch.setenv("PANWATCH_BASE_PATH", prefix)
    monkeypatch.setattr(api.assistant_task_runner, "start_message", lambda *args: None)
    worker = SimpleNamespace(
        record_user_message=lambda *args: SimpleNamespace(id=11),
        create_task=lambda *args: SimpleNamespace(id=42, status="pending", created_at="2026-10-11"),
    )
    async def in_worker(function):
        return function(worker)
    response = asyncio.run(api.create_assistant_task(
        3, SimpleNamespace(content="test"), SimpleNamespace(in_worker=in_worker),
    ))
    assert response["event_url"] == prefix + "/api/assistant/tasks/42/events"
    assert response["snapshot_url"] == prefix + "/api/assistant/tasks/42"


@pytest.mark.parametrize("path,root,expected", [
    ("/api/health", "", "/api/health"),
    ("/apps/panwatch/api/health", "/apps/panwatch", "/api/health"),
    ("/app-other/api/health", "/app", "/app-other/api/health"),
    ("/api/health", "/app", "/api/health"),
])
def test_app_local_middleware_path(path, root, expected):
    assert app_path({"path": path, "root_path": root}) == expected


@pytest.mark.parametrize("path", ["/..%2fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt", "/..%5csecret.txt", "/%2fetc/passwd", "/%00"])
def test_static_url_escapes_are_not_served(deployment, path):
    client, _, _, upstream = deployment
    assert client.get(upstream(path)).status_code == 404


def test_static_index_symlink_fails_closed(tmp_path):
    from fastapi import HTTPException
    root = tmp_path / "public"
    root.mkdir()
    private = tmp_path / "secret.txt"
    private.write_text("PRIVATE_CANARY")
    (root / "index.html").symlink_to(private)
    with pytest.raises(HTTPException) as exc:
        install_spa(FastAPI(), root)
    assert exc.value.status_code == 404


def test_static_index_replacement_fails_closed(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    private = tmp_path / "secret.txt"
    private.write_text("PRIVATE_CANARY")
    index = root / "index.html"
    index.write_text('<head><base href="/" data-panwatch-base><meta name="panwatch-base-path" content=""></head>')
    app = FastAPI()
    install_spa(app, root)
    client = TestClient(app)
    assert client.get("/").status_code == 200
    index.unlink()
    index.symlink_to(private)
    for path in ("/", "/index.html", "/portfolio"):
        response = client.get(path)
        assert response.status_code == 404
        assert "PRIVATE_CANARY" not in response.text
