#!/usr/bin/env python3
"""MCP JSON-RPC 接口测试"""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from src.web.app import app
from src.web.models import Account, Stock, Position


@pytest.fixture
def client(db):
    """每个用例独立的 TestClient，使用内存 DB。"""
    from src.web.database import get_db

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_token(client):
    """设置账号并返回 Bearer token。"""
    # 初始化账号密码
    client.post(
        "/api/auth/setup",
        json={"username": "mcp_test", "password": "password123"},
    )
    r = client.post(
        "/api/auth/login",
        json={"username": "mcp_test", "password": "password123"},
    )
    return r.json()["data"]["token"]


def _call(client, token: str | None, payload: dict):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/api/mcp", json=payload, headers=headers)


def test_mcp_initialize_returns_jsonrpc_result(client, auth_token):
    """initialize 返回 JSON-RPC 标准结构。"""
    r = _call(
        client,
        auth_token,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert data["result"]["protocolVersion"] == "2024-11-05"
    assert data["result"]["serverInfo"]["name"] == "panwatch-mcp"


def test_mcp_tools_list_returns_tools(client, auth_token):
    """tools/list 返回全部工具且响应未被外层包装。"""
    r = _call(
        client,
        auth_token,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert r.status_code == 200
    data = r.json()
    assert "code" not in data  # 中间件放行，不应被包装
    tools = data["result"]["tools"]
    assert len(tools) >= 45
    names = {t["name"] for t in tools}
    assert "mcp.health" in names
    assert "positions.create" in names
    assert "positions.trade" in names


def test_mcp_basic_auth_works(client, db):
    """MCP 支持 HTTP Basic 鉴权。"""
    from src.web.api.auth import set_stored_username, set_password_hash, hash_password

    set_stored_username(db, "basic_user")
    set_password_hash(db, hash_password("basic_pass"))
    db.commit()

    auth_header = "Basic " + base64.b64encode(b"basic_user:basic_pass").decode()
    r = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Authorization": auth_header},
    )
    assert r.status_code == 200
    assert r.json()["result"]["serverInfo"]["name"] == "panwatch-mcp"


def test_mcp_no_auth_returns_401(client):
    """未携带认证信息时返回 401。"""
    r = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "mcp.health", "params": {}},
    )
    assert r.status_code == 401


def test_mcp_readonly_tools_smoke(client, auth_token, db):
    """几个只读工具可正常返回。"""
    # 准备一条自选股
    stock = Stock(symbol="000001", name="平安银行", market="CN", security_type="stock")
    db.add(stock)
    db.commit()

    for tool, args in [
        ("mcp.health", {}),
        ("mcp.version", {}),
        ("market.indices", {}),
        ("stocks.list", {}),
        ("stocks.quotes", {}),
    ]:
        r = _call(
            client,
            auth_token,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            },
        )
        assert r.status_code == 200, f"{tool}: {r.text}"
        data = r.json()
        assert "error" not in data, f"{tool}: {data}"
        assert "result" in data


def test_mcp_position_create_and_trade(client, auth_token, db):
    """持仓创建、加仓、减仓、删除流程使用当前模型字段。"""
    account = Account(name="测试账户", available_funds=100000)
    stock = Stock(symbol="000001", name="平安银行", market="CN", security_type="stock")
    db.add(account)
    db.add(stock)
    db.commit()

    # 创建持仓
    r = _call(
        client,
        auth_token,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "positions.create",
                "arguments": {
                    "account_id": account.id,
                    "stock_id": stock.id,
                    "cost_price": 10.0,
                    "quantity": 100,
                },
            },
        },
    )
    assert r.status_code == 200, r.text
    pos = r.json()["result"]["structuredContent"]
    assert pos["quantity"] == 100
    assert pos["cost_price"] == 10.0
    position_id = pos["id"]

    # 加仓
    r = _call(
        client,
        auth_token,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "positions.trade",
                "arguments": {
                    "position_id": position_id,
                    "action": "add",
                    "quantity": 50,
                    "price": 11.0,
                },
            },
        },
    )
    assert r.status_code == 200, r.text
    trade = r.json()["result"]["structuredContent"]
    assert trade["side"] == "buy"
    assert trade["qty_after"] == 150

    # 减仓
    r = _call(
        client,
        auth_token,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "positions.trade",
                "arguments": {
                    "position_id": position_id,
                    "action": "reduce",
                    "quantity": 30,
                    "price": 12.0,
                },
            },
        },
    )
    assert r.status_code == 200, r.text
    trade = r.json()["result"]["structuredContent"]
    assert trade["side"] == "sell"
    assert trade["qty_after"] == 120

    # 删除持仓
    r = _call(
        client,
        auth_token,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "positions.delete",
                "arguments": {"position_id": position_id},
            },
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["structuredContent"]["success"] is True
    assert db.query(Position).filter(Position.id == position_id).first() is None


def test_mcp_invalid_method_returns_error(client, auth_token):
    """未知 JSON-RPC 方法返回标准 error。"""
    r = _call(
        client,
        auth_token,
        {"jsonrpc": "2.0", "id": 99, "method": "unknown/method", "params": {}},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["jsonrpc"] == "2.0"
    assert data["error"]["code"] == -32601


def test_mcp_response_not_wrapped_by_middleware(client, auth_token):
    """MCP 响应为原生 JSON-RPC，未被 ResponseWrapperMiddleware 包装。"""
    r = _call(
        client,
        auth_token,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "mcp.health", "arguments": {}},
        },
    )
    data = r.json()
    assert "success" not in data
    assert "code" not in data
    assert "result" in data
