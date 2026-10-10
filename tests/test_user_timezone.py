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
from src.modules.market.price_alert_service import parse_expire_at
from src.platform.runtime.config import Settings
from src.platform.scheduling.schedule_parser import parse_cron
from src.platform.scheduling.timezone import to_app_timezone


@pytest.mark.parametrize("zone", ["UTC", "Asia/Shanghai", "America/New_York", "Asia/Kolkata", "Asia/Kathmandu", "Pacific/Kiritimati"])
def test_deployment_tz_override_controls_execution_clock(monkeypatch, zone):
    monkeypatch.setenv("TZ", zone)
    settings = Settings(_env_file=None)
    assert settings.app_timezone == zone
    instant = datetime(2026, 12, 10, tzinfo=timezone.utc)
    assert to_app_timezone(instant) == instant.astimezone(ZoneInfo(zone))
    trigger = parse_cron("0 9 * * *", timezone=settings.app_timezone)
    next_run = trigger.get_next_fire_time(None, instant)
    assert next_run.hour == 9
    assert str(next_run.tzinfo) == zone


@pytest.mark.parametrize("value", ["2026-12-10T16:00", "2026-12-10", "invalid"])
def test_external_expiry_must_identify_an_instant(value):
    with pytest.raises(ValueError):
        parse_expire_at(value)


def test_internal_utc_expiry_and_external_offsets_remain_compatible():
    expected = datetime(2026, 12, 10, 8, 0, 12, 345678)
    assert parse_expire_at(expected) == expected
    assert parse_expire_at("2026-12-10T16:00:12.345678+08:00") == expected
    assert parse_expire_at("2026-12-10T08:00:12.345678Z") == expected
    assert parse_expire_at(None) is None
    assert parse_expire_at("") is None


@pytest.mark.parametrize("zone, start", [
    ("UTC", datetime(2026, 10, 10)),
    ("Asia/Shanghai", datetime(2026, 10, 9, 16)),
    ("America/New_York", datetime(2026, 10, 9, 4)),
    ("Asia/Kolkata", datetime(2026, 10, 9, 18, 30)),
    ("Asia/Kathmandu", datetime(2026, 10, 9, 18, 15)),
    ("Pacific/Kiritimati", datetime(2026, 10, 9, 10)),
])
def test_viewer_midnight_includes_fractional_offsets(zone, start):
    now = datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc)
    actual_start, end = price_alerts._viewer_day_bounds(now, ZoneInfo(zone))
    assert actual_start == start
    assert (end - start).total_seconds() == 24 * 3600


def test_expiry_http_write_read_and_rejected_update_preserve_instant():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Stock(id=1, symbol="AAPL", name="Synthetic Apple", market="US"))
        db.commit()
    app = FastAPI()
    app.include_router(price_alerts.router, prefix="/api/price-alerts")
    def db_override():
        with Session() as db:
            yield db
    app.dependency_overrides[get_db] = db_override
    payload = {"stock_id": 1, "name": "Exact expiry", "enabled": False,
               "condition_group": {"op": "and", "items": [{"type": "price", "op": ">=", "value": 9999}]},
               "expire_at": "2026-12-10T16:00:12.345678+08:00"}
    with TestClient(app) as client:
        created = client.post("/api/price-alerts", json=payload)
        assert created.status_code == 200
        rule = created.json()
        for field in ["expire_at", "created_at", "updated_at"]:
            assert datetime.fromisoformat(rule[field]).tzinfo is not None
        assert datetime.fromisoformat(rule["expire_at"]).astimezone(timezone.utc) == datetime(2026, 12, 10, 8, 0, 12, 345678, tzinfo=timezone.utc)
        for method, url in [(client.post, "/api/price-alerts"), (client.put, f"/api/price-alerts/{rule['id']}")]:
            rejected = method(url, json={**payload, "name": "Rejected update", "expire_at": "2026-12-10T16:00"})
            assert rejected.status_code == 400
        rows = client.get("/api/price-alerts").json()
        assert len(rows) == 1
        assert rows[0]["name"] == "Exact expiry"
        assert rows[0]["expire_at"] == rule["expire_at"]
        cleared = client.put(f"/api/price-alerts/{rule['id']}", json={"expire_at": None})
        assert cleared.status_code == 200
        assert cleared.json()["expire_at"] is None
    engine.dispose()


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
