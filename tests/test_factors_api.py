"""因子权重 API(M5):只读列表 + 手动覆盖 + 路由挂载。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models  # noqa: F401  注册 ORM 模型
from src.platform.persistence.database import Base


def _mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_list_weights_returns_all_market_factor_pairs():
    """GET 列表返回 5 因子 × 3 市场。"""
    from src.modules.strategy.api import factors

    db = _mem_db()
    try:
        res = factors.list_weights(db=db)
        assert "items" in res
        assert len(res["items"]) == 15
    finally:
        db.close()


def test_update_weight_pins_and_sets_value():
    """POST 手动设权重 + pin。"""
    from src.modules.strategy.api import factors

    db = _mem_db()
    try:
        payload = factors.FactorWeightUpdate(weight=1.25, is_pinned=True)
        res = factors.update_weight("alpha_score", "CN", payload, db=db)
        assert res["weight"] == 1.25
        assert res["is_pinned"] is True
    finally:
        db.close()


def test_update_weight_unknown_factor_returns_400():
    """未知因子 → HTTP 400。"""
    from src.modules.strategy.api import factors

    db = _mem_db()
    try:
        payload = factors.FactorWeightUpdate(weight=1.1)
        with pytest.raises(HTTPException) as ei:
            factors.update_weight("bad_factor", "CN", payload, db=db)
        assert ei.value.status_code == 400
    finally:
        db.close()


def test_factors_router_mounted():
    """/api/factors/weights 已挂载到 app(走 OpenAPI schema,兼容自定义 _IncludedRouter)。"""
    from src.bootstrap.application import app

    paths = set(app.openapi().get("paths", {}).keys())
    assert "/api/factors/weights" in paths
    assert "/api/factors/weights/{factor_code}/{market}" in paths
