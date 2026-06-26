"""自选股精华标记 API 测试"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.web.app import app
from src.web.models import Stock


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
    client.post(
        "/api/auth/setup",
        json={"username": "featured_test", "password": "password123"},
    )
    r = client.post(
        "/api/auth/login",
        json={"username": "featured_test", "password": "password123"},
    )
    return r.json()["data"]["token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_list_stocks_featured_first(client, db, auth_token):
    """精华股票在列表中优先返回。"""
    db.add_all([
        Stock(symbol="000001", name="平安银行", market="CN", sort_order=1, is_featured=False),
        Stock(symbol="600519", name="贵州茅台", market="CN", sort_order=2, is_featured=True),
        Stock(symbol="000002", name="万科A", market="CN", sort_order=3, is_featured=False),
    ])
    db.commit()

    r = client.get("/api/stocks", headers=_auth_headers(auth_token))
    assert r.status_code == 200
    items = r.json()["data"]
    assert items[0]["symbol"] == "600519"
    assert items[0]["is_featured"] is True


def test_create_stock_prepends_non_featured(client, db, auth_token):
    """新添加的非精华股票排在非精华区最前。"""
    db.add_all([
        Stock(symbol="000001", name="平安银行", market="CN", sort_order=1, is_featured=True),
        Stock(symbol="000002", name="万科A", market="CN", sort_order=5, is_featured=False),
        Stock(symbol="000003", name="国农科技", market="CN", sort_order=8, is_featured=False),
    ])
    db.commit()

    r = client.post(
        "/api/stocks",
        headers=_auth_headers(auth_token),
        json={"symbol": "601127", "name": "赛力斯", "market": "CN"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["is_featured"] is False
    assert data["sort_order"] == 4

    listed = client.get("/api/stocks", headers=_auth_headers(auth_token)).json()["data"]
    symbols = [item["symbol"] for item in listed]
    assert symbols[0] == "000001"
    assert symbols[1] == "601127"


def test_set_stock_featured(client, db, auth_token):
    """可切换股票精华状态。"""
    stock = Stock(symbol="601127", name="赛力斯", market="CN", sort_order=5, is_featured=False)
    db.add(stock)
    db.commit()
    db.refresh(stock)

    r = client.put(
        f"/api/stocks/{stock.id}/featured",
        headers=_auth_headers(auth_token),
        json={"is_featured": True},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["is_featured"] is True
    assert data["sort_order"] == 1

    r2 = client.put(
        f"/api/stocks/{stock.id}/featured",
        headers=_auth_headers(auth_token),
        json={"is_featured": False},
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["is_featured"] is False
