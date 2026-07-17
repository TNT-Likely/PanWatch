"""数据源管理端点新增能力:is_orphan 标记 + POST /reset-to-seed 温和对账。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.web.api.datasources as ds
from src.web import models as M  # noqa: F401  确保模型注册到 Base.metadata
from src.web.database import Base, get_db
from src.web.models import DataSource


def _client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(ds.router, prefix="/api/datasources")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app), Session


def test_is_orphan_flags_orphan_and_normal_rows_correctly():
    """is_orphan: 孤儿 provider(news/cls)标 True,正常 seed provider(quote/tencent)标 False。"""
    from types import SimpleNamespace

    orphan_row = SimpleNamespace(
        id=1, name="财联社电报", type="news", provider="cls",
        config={}, enabled=True, priority=0, supports_batch=False, test_symbols=[],
    )
    normal_row = SimpleNamespace(
        id=2, name="腾讯行情", type="quote", provider="tencent",
        config={}, enabled=True, priority=0, supports_batch=True, test_symbols=[],
    )

    assert ds._to_response(orphan_row)["is_orphan"] is True
    assert ds._to_response(normal_row)["is_orphan"] is False


def test_reset_to_seed_endpoint_deletes_orphan_and_returns_summary():
    """POST /reset-to-seed: 冒烟 —— 删孤儿行、返回 summary(经中间件包裹前的原始 dict)。"""
    client, Session = _client()

    db = Session()
    db.add(
        DataSource(
            name="财联社电报", type="news", provider="cls", config={},
            enabled=True, priority=0, supports_batch=False, test_symbols=[],
        )
    )
    db.commit()
    db.close()

    resp = client.post("/api/datasources/reset-to-seed")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    deleted_pairs = {(d["type"], d["provider"]) for d in body["deleted"]}
    assert ("news", "cls") in deleted_pairs
    assert "seeded_missing" in body

    db2 = Session()
    remaining = {(s.type, s.provider) for s in db2.query(DataSource).all()}
    assert ("news", "cls") not in remaining
    # 缺失的默认(如东财K线)应被补回
    assert ("kline", "eastmoney") in remaining
    db2.close()
