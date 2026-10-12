"""市场启用机制:核心市场常开,AU 默认关闭,显式开启后生效,再关闭保留数据。"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.marketdata.enablement as em
from src.platform.persistence.database import Base, get_db
from src.platform.persistence import models as M  # noqa: F401


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_default_excludes_au_and_unknown_rejected():
    """默认:仅 CN/HK/US;未知市场代码不可开启。"""
    db = _session()
    assert em.effective_markets(db) == ["CN", "HK", "US"]
    assert em.is_market_enabled(db, "AU") is False
    assert em.is_market_enabled(db, "XX") is False
    try:
        em.set_market_enabled(db, "XX", True)
        assert False, "unknown market must raise"
    except ValueError:
        pass


def test_toggle_roundtrip_and_capabilities():
    """开启后 AU 生效且能力受限;关闭后回到默认,行数据不删。"""
    db = _session()
    em.set_market_enabled(db, "AU", True)
    assert em.is_market_enabled(db, "AU") is True
    assert "AU" in em.effective_markets(db)
    assert em.capability_enabled(db, "AU", "quotes") is True
    assert em.capability_enabled(db, "AU", "paper") is False
    assert em.capability_enabled(db, "CN", "paper") is True  # 核心市场全能力
    em.set_market_enabled(db, "AU", False)
    assert em.is_market_enabled(db, "AU") is False
    assert em.capability_enabled(db, "AU", "quotes") is False


def test_invalid_json_falls_back_to_defaults():
    """非法 JSON 覆盖值被忽略,回到默认。"""
    db = _session()
    from src.platform.persistence.models import AppSettings
    db.add(AppSettings(key=em.SETTING_KEY, value="{not json"))
    db.commit()
    assert em.effective_markets(db) == ["CN", "HK", "US"]


def test_settings_api_validates_market_enablement():
    """settings API:PUT market_enablement 校验 JSON 市场映射,非法值 400。"""
    from src.modules.administration.api.settings import router
    from fastapi import FastAPI

    Session = None
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(router, prefix="/api/settings")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    client = TestClient(app)
    r = client.put("/api/settings/market_enablement", json={"value": '{"AU": true}'})
    assert r.status_code == 200
    r2 = client.put("/api/settings/market_enablement", json={"value": '{"XX": true}'})
    assert r2.status_code == 400
    r3 = client.put("/api/settings/market_enablement", json={"value": "oops"})
    assert r3.status_code == 400
