"""有效市场端点:默认无 AU,开启后出现;市场状态只列有效市场。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.market.api import stocks as stocks_api
from src.platform.marketdata import enablement as em
from src.platform.persistence.database import Base, get_db
from src.platform.persistence import models as M  # noqa: F401


def _client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(stocks_api.router, prefix="/api/stocks")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app), Session


def test_enabled_markets_default_omits_au():
    client, Session = _client()
    r = client.get("/api/stocks/markets/enabled")
    assert r.status_code == 200
    codes = [m["code"] for m in r.json()["markets"]]
    assert codes == ["CN", "HK", "US"]
    assert all(m["currency"] for m in r.json()["markets"])


def test_enabled_markets_after_toggle_and_status_gating():
    client, Session = _client()
    s = Session()
    em.set_market_enabled(s, "AU", True)
    s.close()
    r = client.get("/api/stocks/markets/enabled")
    au = [m for m in r.json()["markets"] if m["code"] == "AU"]
    assert au and au[0]["optional"] is True and au[0]["currency"] == "AUD"
    assert "quotes" in au[0]["capabilities"] and "paper" not in au[0]["capabilities"]
    status = client.get("/api/stocks/markets/status")
    status_codes = [m["code"] for m in status.json()]
    assert "AU" in status_codes
    em.set_market_enabled(Session(), "AU", False)
    status2 = client.get("/api/stocks/markets/status")
    assert "AU" not in [m["code"] for m in status2.json()]
