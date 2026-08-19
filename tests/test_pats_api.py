"""PAT 管理 API 单测:创建(明文仅一次)/列出/吊销，以及吊销后 MCP 端点拒绝。

全自包含:内存 SQLite + TestClient，不连外部。
"""

import src.web.api.mcp as mcp_module
import src.web.api.pats as pats_module  # noqa: F401 (确保模块可导入)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.web.api import mcp as mcp_router_mod
from src.web.api import pats as pats_router_mod
from src.web.database import Base, get_db


def _setup(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    app = FastAPI()
    app.include_router(pats_router_mod.router, prefix="/api/pats")
    app.include_router(mcp_router_mod.router, prefix="/mcp")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(mcp_module, "SessionLocal", Session)
    return TestClient(app), Session


def test_create_pat_returns_plaintext_once(monkeypatch):
    """创建 PAT 返回明文令牌(pwmcp_ 前缀)，列表不含明文"""
    client, _ = _setup(monkeypatch)
    resp = client.post("/api/pats", json={"name": "claude-desktop"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["token"].startswith("pwmcp_")
    assert data["scopes"] == ["mcp:read"]

    listed = client.get("/api/pats").json()["items"]
    assert len(listed) == 1
    assert "token" not in listed[0]  # 列表不返回明文
    assert listed[0]["prefix"].startswith("pwmcp_")


def test_create_pat_rejects_unknown_scope(monkeypatch):
    """创建 PAT 拒绝不支持的 scope"""
    client, _ = _setup(monkeypatch)
    resp = client.post("/api/pats", json={"name": "x", "scopes": ["mcp:write"]})
    assert resp.status_code == 400


def test_revoke_then_mcp_rejects(monkeypatch):
    """吊销 PAT 后，MCP 端点立即拒绝该令牌"""
    client, _ = _setup(monkeypatch)
    created = client.post("/api/pats", json={"name": "temp"}).json()
    token = created["token"]
    pat_id = created["id"]

    # 吊销前可用
    ok = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200

    # 吊销
    dele = client.delete(f"/api/pats/{pat_id}")
    assert dele.status_code == 200

    # 吊销后被拒
    denied = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 401

    # 列表中标记为已吊销
    listed = client.get("/api/pats").json()["items"]
    assert listed[0]["revoked"] is True
