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


def test_symbol_conflict_and_canonical_identity_at_entry():
    """入口归一化:VAS.AX+US 冲突 400;VAS.AX/vas+AU 落库同一规范身份 VAS。"""
    client, Session = _client()
    s = Session()
    em.set_market_enabled(s, "AU", True)
    s.close()

    r = client.post("/api/stocks", json={"symbol": "VAS.AX", "market": "US", "name": "x"})
    assert r.status_code == 400
    assert r.json().get("detail", r.json()).get("code") == "symbol_market_conflict"

    assert client.post("/api/stocks", json={"symbol": "VAS.AX", "market": "AU", "name": "Vanguard"}).status_code == 200
    dup = client.post("/api/stocks", json={"symbol": "vas", "market": "au", "name": "Vanguard"})
    assert dup.status_code == 400
    assert dup.json().get("detail", dup.json()).get("code") == "stock_already_exists"
    s = Session()
    row = s.query(M.Stock).filter_by(market="AU").first()
    assert row.symbol == "VAS"  # 后缀剥离、大写归一,恰好一个身份
    s.close()


def test_quote_lookup_normalizes_suffix(monkeypatch):
    """GET /quotes/VAS.AX?market=AU 命中规范代码 VAS 的行情;显式市场冲突 400。"""
    from src.modules.market.api import quotes as quotes_mod
    monkeypatch.setattr(
        quotes_mod, "md_quote_rows",
        lambda symbols, market: [{"symbol": "VAS", "current_price": 1.0, "change_pct": 0.1,
                                  "change_amount": 0.0, "prev_close": 1.0}],
    )
    client, Session = _client()
    s = Session()
    em.set_market_enabled(s, "AU", True)
    s.close()
    r = client.get("/api/quotes/VAS.AX", params={"market": "AU"})
    assert r.status_code == 200
    assert r.json()["symbol"] == "VAS"
    assert r.json()["current_price"] == 1.0
    assert client.get("/api/quotes/VAS.AX", params={"market": "US"}).status_code == 400


def test_position_creation_refused_when_market_disabled():
    """禁用市场:已保留的 AU 股票不可再新建持仓(既有持仓保留)。"""
    from src.modules.portfolio.api import accounts as accounts_api
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(stocks_api.router, prefix="/api/stocks")
    app.include_router(accounts_api.router, prefix="/api")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    client = TestClient(app)

    s = Session()
    em.set_market_enabled(s, "AU", True)
    s.close()
    acct = client.post("/api/accounts", json={"name": "GF", "available_funds": 0})
    assert acct.status_code == 200
    account_id = acct.json()["id"]
    stock = client.post("/api/stocks", json={"symbol": "VAS", "market": "AU", "name": "VAS"})
    assert stock.status_code == 200
    stock_id = stock.json()["id"]
    payload = {"account_id": account_id, "stock_id": stock_id, "cost_price": 80.0, "quantity": 10}

    # 开启时可建持仓
    assert client.post("/api/positions", json=payload).status_code == 200

    s = Session()
    em.set_market_enabled(s, "AU", False)
    s.close()
    r2 = client.post("/api/positions", json={**payload, "account_id": account_id})
    assert r2.status_code == 400
    assert r2.json().get("detail", r2.json()).get("code") == "market_disabled"
    # 既有持仓保留
    s = Session()
    assert s.query(M.Position).count() == 1
    s.close()


def test_alerts_capability_refused_even_when_au_enabled():
    """AU 无 alerts 能力:市场开启也拒绝规则创建/更新(capability_disabled)。"""
    from src.modules.market.api import price_alerts as alerts_api
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(stocks_api.router, prefix="/api/stocks")
    app.include_router(alerts_api.router, prefix="/api/alerts")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    client = TestClient(app)

    s = Session()
    em.set_market_enabled(s, "AU", True)
    s.close()
    stock = client.post("/api/stocks", json={"symbol": "VAS", "market": "AU", "name": "VAS"})
    stock_id = stock.json()["id"]
    rule = {"stock_id": stock_id, "name": "t", "condition_group": {"op": "and", "items": []}}
    r = client.post("/api/alerts", json=rule)
    assert r.status_code == 400
    assert r.json().get("detail", r.json()).get("code") == "capability_disabled"
    # 核心市场不受影响(条件组需合法:至少一个条件项)
    cn = client.post("/api/stocks", json={"symbol": "600519", "market": "CN", "name": "茅台"})
    ok_rule = {"stock_id": cn.json()["id"], "name": "t",
               "condition_group": {"op": "and", "items": [{"type": "price", "op": ">=", "value": 1.0}]}}
    ok = client.post("/api/alerts", json=ok_rule)
    assert ok.status_code == 200
