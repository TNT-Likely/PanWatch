"""禁用市场的服务端闸门:创建/查询/扫描一律 400/空;开启后放行;再禁用保留数据。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.market.api import klines as klines_api, quotes as quotes_api, stocks as stocks_api
from src.platform.marketdata import enablement as em
from src.platform.persistence.database import Base, get_db
from src.platform.persistence import models as M  # noqa: F401


def _client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    for r, p in ((stocks_api.router, "/api/stocks"), (quotes_api.router, "/api/quotes"), (klines_api.router, "/api/klines")):
        app.include_router(r, prefix=p)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app), Session


def test_disabled_market_rejected_across_surfaces():
    client, Session = _client()
    assert client.post("/api/stocks", json={"symbol": "VAS", "name": "VAS", "market": "AU"}).status_code == 400
    body = client.post("/api/stocks", json={"symbol": "VAS", "name": "VAS", "market": "AU"}).json()
    # 裸 TestClient 无响应包装中间件,HTTPException 走 FastAPI 默认 {"detail": {...}} 形
    assert body.get("detail", body).get("code") == "market_disabled"
    assert client.get("/api/quotes/VAS", params={"market": "AU"}).status_code == 400
    assert client.get("/api/klines/VAS", params={"market": "AU"}).status_code == 400


def test_enabled_then_disabled_preserves_rows():
    client, Session = _client()
    s = Session()
    em.set_market_enabled(s, "AU", True)
    s.close()
    r = client.post("/api/stocks", json={"symbol": "VAS", "name": "Vanguard AU", "market": "AU"})
    assert r.status_code == 200
    s = Session()
    em.set_market_enabled(s, "AU", False)
    s.close()
    # 禁用后:不再放行新查询,但已有行仍在
    assert client.get("/api/quotes/VAS", params={"market": "AU"}).status_code == 400
    s = Session()
    assert s.query(M.Stock).filter_by(symbol="VAS", market="AU").count() == 1
    s.close()


def test_core_markets_unaffected():
    client, Session = _client()
    assert client.get("/api/quotes/600519", params={"market": "CN"}).status_code in (200, 404)  # 404=无行情数据,不是 400 闸门
    assert client.post("/api/stocks", json={"symbol": "600519", "name": "贵州茅台", "market": "CN"}).status_code == 200


def test_md_rows_guard_skips_disabled():
    """marketdata_client 守卫:禁用市场不触达 vendor(直接空)。"""
    from unittest.mock import patch
    from src.platform.marketdata import marketdata_client as mc
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    # 守卫内部走 SessionLocal:换成测试内存库,避免读到开发库的启用状态
    with patch.object(mc, "get_market_data") as gmd, \
         patch("src.platform.persistence.database.SessionLocal", Session):
        assert mc.md_quote_rows(["VAS"], "AU") == []
        gmd.assert_not_called()
        assert mc.md_quote_rows([], "CN") == []
    s.close()
