"""认证模式:AUTH_MODE=proxy 时信任网关身份头,local 保持用户名密码 + JWT。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as M  # noqa: F401 (确保模型注册到 Base)
from src.modules.administration.api import auth as auth_api
from src.platform.persistence.database import Base, get_db


def _client(tmp_path, monkeypatch, mode: str) -> TestClient:
    """构造仅挂载 auth router 的测试应用。

    AUTH_MODE 是模块级常量(import 时读 env),这里直接 patch 模块属性
    以覆盖两种模式;get_current_user 运行时读取的正是该模块全局。
    """
    monkeypatch.setattr(auth_api, "AUTH_MODE", mode)
    # local 模式签发 JWT 走 get_jwt_secret(),固定测试密钥避免触碰真实 DB
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(auth_api, "_jwt_secret", None)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(auth_api.router, prefix="/api/auth")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


# --------------------------- proxy 模式(网关认证卸载)---------------------------

def test_proxy_status(tmp_path, monkeypatch):
    """status 报告 proxy 模式且 initialized=true,并给出统一登出地址。"""
    c = _client(tmp_path, monkeypatch, "proxy")
    data = c.get("/api/auth/status").json()
    assert data["mode"] == "proxy"
    assert data["initialized"] is True
    assert data["logout_url"]


def test_proxy_login_and_setup_disabled(tmp_path, monkeypatch):
    """网关模式下应用内登录/设置密码接口被禁用。"""
    c = _client(tmp_path, monkeypatch, "proxy")
    assert c.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 400
    assert c.post("/api/auth/setup", json={"username": "a", "password": "b"}).status_code == 400


def test_proxy_requires_gateway_header(tmp_path, monkeypatch):
    """受保护端点缺身份头 → 401(网关认证未通过)。"""
    c = _client(tmp_path, monkeypatch, "proxy")
    r = c.get("/api/auth/me")
    assert r.status_code == 401
    assert "网关" in r.json()["detail"]


def test_proxy_trusts_gateway_header(tmp_path, monkeypatch):
    """带网关注入的身份头 → 认证通过,用户名来自头部。"""
    c = _client(tmp_path, monkeypatch, "proxy")
    r = c.get("/api/auth/me", headers={"X-Homenas-Auth-User": "shentar"})
    assert r.status_code == 200
    assert r.json()["user"] == "shentar"


def test_proxy_change_password_disabled(tmp_path, monkeypatch):
    """网关模式下应用内改密码被禁用。"""
    c = _client(tmp_path, monkeypatch, "proxy")
    r = c.post(
        "/api/auth/change-password",
        json={"username": "a", "password": "newpass"},
        headers={"X-Homenas-Auth-User": "shentar"},
    )
    assert r.status_code == 400


def test_proxy_custom_header_name(tmp_path, monkeypatch):
    """PROXY_AUTH_HEADER 可配置为其他头名。"""
    monkeypatch.setattr(auth_api, "PROXY_AUTH_HEADER", "X-Forwarded-User")
    c = _client(tmp_path, monkeypatch, "proxy")
    assert c.get("/api/auth/me", headers={"X-Homenas-Auth-User": "shentar"}).status_code == 401
    r = c.get("/api/auth/me", headers={"X-Forwarded-User": "shentar"})
    assert r.status_code == 200
    assert r.json()["user"] == "shentar"


# --------------------------- local 模式(回归)---------------------------

def test_local_status_before_setup(tmp_path, monkeypatch):
    """local 模式初始状态:mode=local、initialized=false。"""
    c = _client(tmp_path, monkeypatch, "local")
    data = c.get("/api/auth/status").json()
    assert data["mode"] == "local"
    assert data["initialized"] is False


def test_local_setup_then_login(tmp_path, monkeypatch):
    """首次 setup 发 token,随后用同样凭据 login 成功。"""
    c = _client(tmp_path, monkeypatch, "local")
    r = c.post("/api/auth/setup", json={"username": "admin", "password": "secret1"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    # 凭据正确
    r = c.post("/api/auth/login", json={"username": "admin", "password": "secret1"})
    assert r.status_code == 200
    assert r.json()["token"]

    # 凭据错误
    r = c.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401

    # 带 token 访问受保护端点
    r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_local_requires_token_after_setup(tmp_path, monkeypatch):
    """设置密码后,受保护端点无 token → 401。"""
    c = _client(tmp_path, monkeypatch, "local")
    c.post("/api/auth/setup", json={"username": "admin", "password": "secret1"})
    assert c.get("/api/auth/me").status_code == 401


def test_local_open_before_setup(tmp_path, monkeypatch):
    """未设置密码时受保护端点放行(首次 setup 流程)。"""
    c = _client(tmp_path, monkeypatch, "local")
    r = c.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["user"] == "guest"
