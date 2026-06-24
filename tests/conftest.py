"""共用 pytest fixtures。

默认情况下所有通知发送函数被替换为 no-op，避免单测误发通知。
传入 --notify 参数可恢复真实发送（用于集成测试）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def pytest_itemcollected(item):
    """用测试函数的中文 docstring 替换 pytest -v 输出中的节点名。"""
    doc = (item.function.__doc__ or "").strip().split("\n")[0]
    if doc:
        item._nodeid = f"{item.parent.nodeid}::{doc}"


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--notify",
        action="store_true",
        default=False,
        help="启用真实通知发送（默认关闭）",
    )


@pytest.fixture(autouse=True)
def _suppress_notifications(request, monkeypatch):
    """自动屏蔽通知发送，除非传入 --notify。"""
    if request.config.getoption("--notify"):
        return

    # patch NotifierManager.notify / notify_with_result
    monkeypatch.setattr(
        "src.core.notifier.NotifierManager.notify",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "src.core.notifier.NotifierManager.notify_with_result",
        AsyncMock(return_value={"success": True, "suppressed": True}),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _mock_stock_link_platform(monkeypatch):
    """避免 stock_link 模块访问数据库读取平台设置。"""
    monkeypatch.setattr(
        "src.core.stock_link.get_platform",
        lambda: "xueqiu",
    )


@pytest.fixture(autouse=True)
def _clear_market_caches():
    """清空采集层内存缓存,避免用例间互相污染(K线/报价/资金流等现按 TTL 缓存)。"""
    from src.collectors import (
        akshare_collector,
        capital_flow_collector,
        discovery_collector,
        kline_collector,
    )

    def _clear():
        kline_collector.clear_kline_cache()
        akshare_collector._QUOTE_CACHE.clear()
        capital_flow_collector._FLOW_CACHE.clear()
        discovery_collector._DISCOVERY_CACHE.clear()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True, scope="session")
def _ensure_db_schema():
    """确保真实 DB 引擎已建表(CI 全新环境 data/panwatch.db 无表时兜底)。

    写库用例请用下方 ``db`` fixture(内存 SQLite),勿用 SessionLocal()。
    """
    from src.web import models  # noqa: F401  注册所有 ORM 模型到 Base.metadata
    from src.web.database import Base, engine

    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    """每个用例独立的内存 SQLite 会话，避免污染 data/panwatch.db。"""
    from src.web import models  # noqa: F401  注册所有 ORM 模型到 Base.metadata
    from src.web.database import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 共用工厂 fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_account() -> dict:
    """模拟盘账户数据。"""
    return {
        "id": 1,
        "name": "测试账户",
        "initial_capital": 100_000.0,
        "current_capital": 100_000.0,
    }


@pytest.fixture
def mock_signal() -> dict:
    """模拟策略信号。"""
    return {
        "strategy": "trend_follow",
        "symbol": "002837",
        "market": "CN",
        "action": "BUY",
        "confidence": 0.85,
        "reason": "趋势向上突破",
    }
