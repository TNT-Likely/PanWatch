"""MCP Server + PAT 鉴权测试。

全用内存库与 TestClient,不触网:tools/call 只测纯 DB 工具(get_watchlist),
覆盖协议握手/发现/调用/鉴权拒绝(缺失/无效/吊销)/JWT 不能进 MCP。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.web.api import mcp as mcp_api
from src.web.api import pats as pats_api
from src.web.database import Base, get_db


@pytest.fixture()
def client_and_session(monkeypatch):
    """内存库 + 挂 pats(/api/pats)与 mcp(/mcp)的测试应用。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    # 审计日志写库也指向内存库,避免污染真实库
    monkeypatch.setattr(mcp_api, "SessionLocal", TestSession)

    app = FastAPI()
    app.include_router(pats_api.router, prefix="/api/pats")
    app.include_router(mcp_api.router, prefix="/mcp")
    app.dependency_overrides[get_db] = _db
    return TestClient(app), TestSession


def _create_pat(client, name="test") -> str:
    resp = client.post("/api/pats", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _rpc(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        body["id"] = rid
    if params is not None:
        body["params"] = params
    return body


def test_missing_auth_rejected(client_and_session):
    """缺少 Authorization → 401"""
    client, _ = client_and_session
    r = client.post("/mcp", json=_rpc("initialize"))
    assert r.status_code == 401


def test_jwt_cannot_enter_mcp(client_and_session):
    """非 PAT(如 JWT)的 Bearer → 403,JWT 不能进 MCP"""
    client, _ = client_and_session
    r = client.post(
        "/mcp",
        json=_rpc("initialize"),
        headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.fake.jwt"},
    )
    assert r.status_code == 403


def test_invalid_pat_rejected(client_and_session):
    """PAT 前缀正确但库里查不到 → 401"""
    client, _ = client_and_session
    r = client.post(
        "/mcp",
        json=_rpc("initialize"),
        headers={"Authorization": "Bearer pwmcp_notarealtoken"},
    )
    assert r.status_code == 401


def test_initialize_handshake(client_and_session):
    """initialize 返回 protocolVersion / capabilities / serverInfo"""
    client, _ = client_and_session
    token = _create_pat(client)
    r = client.post(
        "/mcp",
        json=_rpc("initialize", {"protocolVersion": "2025-06-18"}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    res = data["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert "tools" in res["capabilities"]
    assert res["serverInfo"]["name"]


def test_tools_list_discovery(client_and_session):
    """tools/list 暴露 5 个只读工具,含 inputSchema"""
    client, _ = client_and_session
    token = _create_pat(client)
    r = client.post(
        "/mcp",
        json=_rpc("tools/list"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {
        "get_portfolio",
        "get_stock_quote",
        "get_technical_analysis",
        "get_stock_suggestions",
        "get_watchlist",
    }
    assert all("inputSchema" in t for t in tools)


def test_tools_call_and_audit_log(client_and_session):
    """tools/call 执行纯 DB 工具并落审计日志"""
    client, TestSession = client_and_session
    token = _create_pat(client)
    r = client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "get_watchlist", "arguments": {}}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["isError"] is False
    assert res["content"][0]["type"] == "text"
    assert isinstance(res["content"][0]["text"], str)

    # 审计日志落库
    from src.web.models import MCPCallLog

    db = TestSession()
    try:
        logs = db.query(MCPCallLog).all()
        assert len(logs) == 1
        assert logs[0].tool_name == "get_watchlist"
        assert logs[0].status == "ok"
    finally:
        db.close()


def test_tools_call_unknown_tool(client_and_session):
    """调用不在白名单的工具 → JSON-RPC 参数错误"""
    client, _ = client_and_session
    token = _create_pat(client)
    r = client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "rm_rf", "arguments": {}}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32602


def test_revoked_pat_rejected(client_and_session):
    """吊销后的 PAT 立即失效 → 401"""
    client, _ = client_and_session
    create = client.post("/api/pats", json={"name": "tmp"}).json()
    token = create["token"]
    pat_id = create["id"]
    # 吊销
    assert client.delete(f"/api/pats/{pat_id}").status_code == 200
    r = client.post(
        "/mcp",
        json=_rpc("tools/list"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_notification_returns_202(client_and_session):
    """通知类消息(无 id)无需响应 → 202"""
    client, _ = client_and_session
    token = _create_pat(client)
    r = client.post(
        "/mcp",
        json=_rpc("notifications/initialized", rid=None),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202
