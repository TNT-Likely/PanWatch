"""ETF overview API 端点测试 —— 通过 TestClient 验证路由 + 鉴权 + 兜底。"""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import src.web.models  # noqa: F401  注册 ORM 模型
from src.web.database import Base
from src.web.app import app
from src.web.api.auth import get_current_user


def _client(monkeypatch, overview_ret=None, get_mock=None):
    """构造绕过鉴权的 TestClient,并 mock 采集器。"""
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    get_mock = get_mock or MagicMock(return_value=overview_ret or {})
    monkeypatch.setattr("src.collectors.etf_collector.get_etf_overview", get_mock)
    return TestClient(app), get_mock


def test_etf_overview_returns_aggregated(monkeypatch):
    """GET /api/stocks/etf/{code}/overview 返回聚合数据。"""
    fake = {
        "symbol": "510300",
        "spot": {"price": 5.04, "iopv": 5.05, "premium_pct": 0.13},
        "holdings": [{"symbol": "600519", "weight_pct": 4.74}],
        "nav_history": [{"date": "2025-01-02", "unit_nav": 3.90}],
    }
    client, get_mock = _client(monkeypatch, overview_ret=fake)
    try:
        res = client.get("/api/stocks/etf/510300/overview")
        assert res.status_code == 200
        assert res.json()["data"] == fake
        get_mock.assert_called_once_with("510300", top=30, nav_days=180)
    finally:
        app.dependency_overrides.clear()


def test_etf_overview_empty_code_returns_400(monkeypatch):
    """空代码返回 400(路由层校验)。"""
    client, _ = _client(monkeypatch)
    try:
        res = client.get("/api/stocks/etf/%20/overview")
        assert res.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_etf_overview_passes_query_params(monkeypatch):
    """top/nav_days 查询参数透传到采集器。"""
    client, get_mock = _client(monkeypatch, overview_ret={"symbol": "510300"})
    try:
        res = client.get("/api/stocks/etf/510300/overview?top=50&nav_days=365")
        assert res.status_code == 200
        get_mock.assert_called_once_with("510300", top=50, nav_days=365)
    finally:
        app.dependency_overrides.clear()


def test_etf_overview_requires_auth():
    """未携带 token 返回 401(鉴权生效)。"""
    # 不覆盖依赖,直接裸调
    client = TestClient(app)
    res = client.get("/api/stocks/etf/510300/overview")
    assert res.status_code == 401
