"""Timezone boundaries visible to UTC users and DST viewers."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.market.api import price_alerts
from src.modules.assistant.schemas import ConversationDTO, MessageDTO
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import PriceAlertHit, PriceAlertRule, Stock
from src.web.datetime import as_utc


def test_utc_dto_restores_sqlite_timestamp_independently_of_server_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    for dto in [ConversationDTO(id=1, created_at=datetime(2026, 10, 9, 8)), MessageDTO(id=1, role="user", content="test", created_at=datetime(2026, 10, 9, 8))]:
        assert dto.model_dump(mode="json")["created_at"] == "2026-10-09T08:00:00Z"
    assert as_utc(datetime(2026, 10, 9, 16, tzinfo=ZoneInfo("Asia/Shanghai"))) == datetime(2026, 10, 9, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize("now, hours", [(datetime(2026, 3, 8, 12, tzinfo=timezone.utc), 23), (datetime(2026, 11, 1, 12, tzinfo=timezone.utc), 25)])
def test_viewer_day_uses_dst_midnights(now, hours):
    start, end = price_alerts._viewer_day_bounds(now, ZoneInfo("America/New_York"))
    assert (end - start).total_seconds() == hours * 3600


def test_today_http_filter_respects_viewer_day_and_excludes_tomorrow(monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 9, 20, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(price_alerts, "datetime", FixedClock)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Stock(id=1, symbol="AAPL", name="Synthetic Apple", market="US"))
        db.add(PriceAlertRule(id=1, stock_id=1, name="Synthetic timezone rule"))
        db.add_all([PriceAlertHit(rule_id=1, stock_id=1, trigger_time=datetime(2026, 10, day, hour), trigger_bucket=f"{day}-{hour}") for day, hour in [(9, 15), (9, 16), (9, 23), (10, 0)]])
        db.commit()
    app = FastAPI()
    app.include_router(price_alerts.router, prefix="/api/price-alerts")
    def db_override():
        with Session() as db:
            yield db
    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        utc = client.get("/api/price-alerts/hits/today?timezone=UTC")
        shanghai = client.get("/api/price-alerts/hits/today?timezone=Asia%2FShanghai")
        assert utc.status_code == shanghai.status_code == 200
        def instants(response):
            return [datetime.fromisoformat(hit["trigger_time"]).astimezone(timezone.utc).isoformat() for hit in response.json()]
        assert instants(utc) == ["2026-10-09T23:00:00+00:00", "2026-10-09T16:00:00+00:00", "2026-10-09T15:00:00+00:00"]
        assert instants(shanghai) == ["2026-10-10T00:00:00+00:00", "2026-10-09T23:00:00+00:00", "2026-10-09T16:00:00+00:00"]
        assert client.get("/api/price-alerts/hits/today?timezone=Invalid%2FZone").status_code == 400
    engine.dispose()
