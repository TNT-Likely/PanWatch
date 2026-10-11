from datetime import datetime, timedelta, timezone
import importlib
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import Stock, PriceAlertRule
from src.modules.market.api import price_alerts
from src.modules.market.price_alert_engine import PriceAlertEngine, _day_key


@pytest.mark.parametrize('market', ['TSX', 'TSXV'])
@pytest.mark.parametrize('instant,day', [
    (datetime(2026, 7, 8, 1, tzinfo=timezone.utc), '2026-07-07'),
    (datetime(2026, 1, 8, 4, 30, tzinfo=timezone.utc), '2026-01-07'),
    (datetime(2026, 7, 8, 4, 30, tzinfo=timezone.utc), '2026-07-08'),
    (datetime(2026, 1, 8, 5, 30, tzinfo=timezone.utc), '2026-01-08'),
])
def test_canadian_health_quota_uses_same_toronto_day_as_engine(monkeypatch, market, instant, day):
    health = importlib.import_module('src.modules.market.monitoring_health')
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(health, 'datetime', Clock)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        stock = Stock(symbol='T', market=market, name='TELUS')
        db.add(stock); db.flush()
        quota_day = (instant.date() - timedelta(days=1)).isoformat()
        rule = PriceAlertRule(stock_id=stock.id, enabled=True, trigger_date=quota_day, trigger_count_today=3, max_triggers_per_day=3)
        db.add(rule); db.commit()
        expected_used = 3 if quota_day == day else 0
        allowed, reason = PriceAlertEngine()._can_trigger(rule, instant, bypass_market_hours=True)
        assert allowed == (expected_used == 0)
        if expected_used:
            assert reason == 'daily_limit'
    app = FastAPI(); app.include_router(price_alerts.router, prefix='/price-alerts')
    def db_session():
        with sessions() as db: yield db
    app.dependency_overrides[get_db] = db_session
    with TestClient(app) as client:
        result = client.get('/price-alerts/health')
        assert result.status_code == 200
        quota = result.json()['items'][0]['quota']
        assert _day_key(instant, market) == day
        assert quota['day'] == _day_key(instant, market)
        assert quota['used'] == expected_used
        assert quota['exhausted'] == (expected_used == 3)
        assert (result.json()['items'][0]['status'] == 'daily_limit') == (expected_used == 3)
    engine.dispose()
