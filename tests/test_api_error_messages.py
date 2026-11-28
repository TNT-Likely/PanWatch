"""API 错误信封里的校验错误信息必须可直接阅读。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base, get_db
from src.modules.market.api import stocks
from src.modules.portfolio.api import accounts
from src.web.response import ResponseWrapperMiddleware


def _client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app = FastAPI()
    # 与真实应用一致：/api 前缀 + 统一错误信封中间件。
    app.include_router(stocks.router, prefix="/api/stocks")
    app.include_router(accounts.router, prefix="/api")
    app.add_middleware(ResponseWrapperMiddleware)
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app), session, engine


def test_validation_error_is_reported_as_a_readable_message():
    """校验失败时，错误信封里的 message 是可读文案，不是整段 JSON。"""
    client, session, engine = _client()
    try:
        response = client.post(
            "/api/stocks",
            json={
                "symbol": "600519",
                "name": "贵州茅台",
                "market": "CN",
                "asset_type": "bond",
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert body["success"] is False
        assert body["error_code"] == "http_422"
        assert body["message"]
        # 之前这里塞的是 FastAPI 校验错误列表的 JSON 字符串，前端 toast 无法阅读。
        assert not body["message"].startswith("[")
        assert "{" not in body["message"]
    finally:
        session.close()
        engine.dispose()


def test_error_envelope_keeps_its_shape_for_successful_requests():
    """成功响应仍然是统一信封（code/success/data/message）。"""
    client, session, engine = _client()
    try:
        response = client.post(
            "/api/stocks", json={"symbol": "600519", "name": "贵州茅台", "market": "CN"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["code"] == 0
        assert body["data"]["symbol"] == "600519"
    finally:
        session.close()
        engine.dispose()
