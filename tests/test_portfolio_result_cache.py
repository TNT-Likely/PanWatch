"""组合基准/归因结果缓存:按持仓指纹缓存,持仓变动即失效,空结果不缓存。

重建全持仓 NAV(逐只拉 K 线)很贵,首页又频繁请求基准/归因。这里按持仓指纹缓存结果,
命中时跳过行情/K 线;持仓变化指纹即变 → 重算;失败/空结果不缓存,避免冻住瞬时故障。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.modules.portfolio.portfolio_benchmark as pb
import src.platform.persistence.models as M
from src.modules.portfolio.api import accounts as accounts_api
from src.platform.persistence.database import Base

_HOLDINGS = [{"symbol": "600519", "market": "CN", "quantity": 100, "market_value": 100.0, "fx": 1.0}]


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    accounts_api._PORTFOLIO_RESULT_CACHE.clear()
    try:
        yield s
    finally:
        s.close()
        accounts_api._PORTFOLIO_RESULT_CACHE.clear()


def _add_position(db, symbol: str, qty: float):
    acc = db.query(M.Account).first()
    if not acc:
        acc = M.Account(name="t", available_funds=0, enabled=True)
        db.add(acc)
        db.flush()
    st = M.Stock(symbol=symbol, name=symbol, market="CN")
    db.add(st)
    db.flush()
    db.add(M.Position(account_id=acc.id, stock_id=st.id, cost_price=1.0, quantity=qty))
    db.commit()


def test_benchmark_result_cached(db, monkeypatch):
    """同一持仓的基准请求只计算一次,第二次命中缓存。"""
    _add_position(db, "600519", 100)
    calls = {"n": 0}

    def fake_build(holdings, days=60, benchmark_code="000300"):
        calls["n"] += 1
        return {"excess_return": 1.23}

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    r1 = accounts_api.portfolio_benchmark(days=60, benchmark="000300", db=db)
    r2 = accounts_api.portfolio_benchmark(days=60, benchmark="000300", db=db)
    assert calls["n"] == 1, f"第二次应命中缓存,实际计算 {calls['n']} 次"
    assert r1 == r2 == {"excess_return": 1.23}


def test_benchmark_empty_not_cached(db, monkeypatch):
    """数据不足(build 返回空)不缓存,下次仍会重算。"""
    _add_position(db, "600519", 100)
    calls = {"n": 0}

    def fake_build(*a, **k):
        calls["n"] += 1
        return None

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    r1 = accounts_api.portfolio_benchmark(db=db)
    accounts_api.portfolio_benchmark(db=db)
    assert calls["n"] == 2, "空结果不应缓存,应重算"
    assert r1.get("empty") is True


def test_benchmark_cache_invalidates_on_holdings_change(db, monkeypatch):
    """持仓变化(指纹变)后应重新计算,不返回旧缓存。"""
    _add_position(db, "600519", 100)
    calls = {"n": 0}

    def fake_build(*a, **k):
        calls["n"] += 1
        return {"excess_return": float(calls["n"])}

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    accounts_api.portfolio_benchmark(db=db)  # 计算 1,写缓存
    _add_position(db, "000001", 50)  # 持仓变化 → 指纹变
    accounts_api.portfolio_benchmark(db=db)  # 应重算
    assert calls["n"] == 2, "持仓变化后缓存应失效"


def test_attribution_result_cached(db, monkeypatch):
    """归因结果同样按持仓指纹缓存。"""
    _add_position(db, "600519", 100)
    calls = {"n": 0}

    def fake_attr(holdings, days=60, benchmark_code="000300"):
        calls["n"] += 1
        return [{"symbol": "600519", "contribution_pct": 1.0}]

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_attribution", fake_attr)

    r1 = accounts_api.portfolio_attribution(db=db)
    r2 = accounts_api.portfolio_attribution(db=db)
    assert calls["n"] == 1, f"第二次应命中缓存,实际 {calls['n']} 次"
    assert r1 == r2
