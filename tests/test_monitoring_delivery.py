"""Fault and recovery contracts for durable price-alert monitoring."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.modules.market import alert_delivery, price_alert_engine
from src.modules.market.alert_delivery import AlertDeliveryWorker, enqueue, now_utc, retry_delivery
from src.modules.market.monitoring_health import monitoring_health
from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    NotifyChannel, PriceAlertDelivery, PriceAlertHealth, PriceAlertHit,
    PriceAlertRule, PriceAlertScanHealth, Stock, NotificationEvent,
)


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'delivery.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(price_alert_engine, 'SessionLocal', sessions)
    monkeypatch.setattr('src.platform.scheduling.trading_calendar.is_trading_day', lambda *args: True)
    yield sessions
    engine.dispose()


def seed(factory, *, channels=1, once=False):
    with factory() as db:
        stock = Stock(symbol='600519', market='CN', name='QA')
        db.add(stock); db.flush()
        ids = []
        for n in range(channels):
            channel = NotifyChannel(name=f'QA {n}', type='wecom', config={'key':'PRIVATE_SECRET'}, enabled=True, is_default=True)
            db.add(channel); db.flush(); ids.append(channel.id)
        rule = PriceAlertRule(stock_id=stock.id, condition_group={'op':'and','items':[{'type':'price','op':'>=','value':5}]}, market_hours_mode='always', cooldown_minutes=0, max_triggers_per_day=0, repeat_mode='once' if once else 'repeat', notify_channel_ids=ids)
        db.add(rule); db.commit()
        return rule.id


def hit(factory, rule_id):
    return price_alert_engine.PriceAlertEngine()._persist_hit(rule_id, price_alert_engine._utc_now(), {'quote': {'current_price':6}}, 6)


def test_hit_inbox_and_outbox_are_one_transaction_and_once_rule_recovers(factory, monkeypatch):
    rule_id = seed(factory, once=True)
    hit_id = hit(factory, rule_id)
    with factory() as db:
        assert db.query(PriceAlertHit).count() == db.query(NotificationEvent).count() == db.query(PriceAlertDelivery).count() == 1
        assert not db.get(PriceAlertRule, rule_id).enabled
        assert db.get(PriceAlertHit, hit_id).notify_error == 'pending'
    worker = AlertDeliveryWorker(factory)
    claim = worker.claim()  # simulated process death after committing lease
    with factory() as db:
        db.get(PriceAlertDelivery, claim['id']).lease_until = now_utc() - timedelta(seconds=1)
        db.commit()
    recovered = AlertDeliveryWorker(factory).claim()
    assert recovered['id'] == claim['id'] and recovered['token'] != claim['token']
    worker.finish(claim, ok=True)  # stale owner cannot finish the new attempt
    with factory() as db:
        assert db.get(PriceAlertDelivery, claim['id']).status == 'sending'
    worker.finish(recovered, ok=True)
    with factory() as db:
        assert db.get(PriceAlertHit, hit_id).notify_success
        row = db.get(PriceAlertDelivery, claim['id'])
        assert row.attempts == 2 and row.event_id.startswith(f'price-alert:{hit_id}:1:')


def test_enqueue_failure_rolls_back_hit_rule_and_inbox(factory, monkeypatch):
    rule_id = seed(factory)
    monkeypatch.setattr(alert_delivery, 'enqueue', lambda *args: (_ for _ in ()).throw(RuntimeError('disk failure')))
    with pytest.raises(RuntimeError):
        hit(factory, rule_id)
    with factory() as db:
        assert db.query(PriceAlertHit).count() == db.query(NotificationEvent).count() == db.query(PriceAlertDelivery).count() == 0
        assert db.get(PriceAlertRule, rule_id).last_trigger_at is None


def test_claim_is_exclusive_and_successful_channels_are_not_retried(factory):
    rule_id = seed(factory, channels=2); hit_id = hit(factory, rule_id)
    a, b = AlertDeliveryWorker(factory), AlertDeliveryWorker(factory)
    first, second = a.claim(), b.claim()
    assert first['id'] != second['id'] and a.claim() is None
    a.finish(first, ok=True); b.finish(second, error='channel_send_failed')
    with factory() as db:
        assert not db.get(PriceAlertHit, hit_id).notify_success
        failed = db.get(PriceAlertDelivery, second['id'])
        assert failed.status == 'retry' and failed.next_attempt_at > now_utc()
        retry_delivery(db, failed.id)
        with pytest.raises(ValueError):
            retry_delivery(db, first['id'])
    assert a.claim()['id'] == second['id']


def test_exhausted_crash_lease_becomes_failed_and_manual_retry_retains_count(factory):
    hit_id = hit(factory, seed(factory)); worker = AlertDeliveryWorker(factory); claim = worker.claim()
    with factory() as db:
        row = db.get(PriceAlertDelivery, claim['id']); row.attempts = 5; row.lease_until = now_utc() - timedelta(seconds=1); db.commit()
    assert worker.claim() is None
    with factory() as db:
        row = db.get(PriceAlertDelivery, claim['id'])
        assert row.status == 'failed' and row.error_code == 'attempts_exhausted'
        assert db.get(PriceAlertHit, hit_id).notify_error == 'attempts_exhausted'
        retry_delivery(db, row.id)
        assert row.attempts == 5 and row.max_attempts == 10


def test_channel_disabled_and_no_default_are_visible_without_false_success(factory):
    rule_id = seed(factory); hit_id = hit(factory, rule_id)
    with factory() as db:
        db.get(NotifyChannel, 1).enabled = False; db.commit()
    asyncio.run(AlertDeliveryWorker(factory).drain())
    with factory() as db:
        row = db.query(PriceAlertDelivery).one()
        assert row.status == 'blocked' and row.error_code == 'channel_unavailable'
        with pytest.raises(ValueError): retry_delivery(db, row.id)
        assert not db.get(PriceAlertHit, hit_id).notify_success
        rule = db.get(PriceAlertRule, rule_id); rule.notify_channel_ids = []; rule.last_trigger_at = None; db.commit()
        # New hit bucket, still no enabled default channel.
        new_hit = PriceAlertHit(rule_id=rule_id, stock_id=rule.stock_id, trigger_bucket='second')
        db.add(new_hit); db.flush(); enqueue(db, new_hit, rule, 'QA', 'QA'); db.commit()
        row = db.query(PriceAlertDelivery).filter_by(hit_id=new_hit.id).one()
        assert row.status == 'blocked' and row.error_code == 'channel_missing'


def test_worker_errors_are_safe_and_retry_backoff_is_persisted(factory, monkeypatch):
    hit_id = hit(factory, seed(factory))
    monkeypatch.setattr(alert_delivery.NotifierManager, 'notify_with_result', AsyncMock(return_value={'success':False,'error':'https://secret.invalid/PRIVATE_SECRET'}))
    assert asyncio.run(AlertDeliveryWorker(factory).drain())['processed'] == 1
    with factory() as db:
        row = db.query(PriceAlertDelivery).one()
        assert row.status == 'retry' and row.error_code == 'channel_send_failed'
        assert 'PRIVATE_SECRET' not in str(monitoring_health(db))
        assert 'PRIVATE_SECRET' not in db.get(PriceAlertHit, hit_id).notify_error


def test_dry_run_does_not_advance_live_health_or_enqueue(factory, monkeypatch):
    seed(factory)
    engine = price_alert_engine.PriceAlertEngine()
    monkeypatch.setattr(engine, '_fetch_quotes_map', AsyncMock(return_value={('CN','600519'):{'current_price':6}}))
    result = asyncio.run(engine.scan_once(dry_run=True, bypass_market_hours=True))
    assert result['triggered'] == 1
    with factory() as db:
        assert db.query(PriceAlertHealth).count() == db.query(PriceAlertScanHealth).count() == db.query(PriceAlertHit).count() == 0


def test_check_failures_recover_without_claiming_missing_condition_is_healthy(factory, monkeypatch):
    rule_id = seed(factory)
    engine = price_alert_engine.PriceAlertEngine()
    monkeypatch.setattr(price_alert_engine, 'quote_date_is_current', lambda *a: True)
    monkeypatch.setattr(engine, '_fetch_quotes_map', AsyncMock(side_effect=RuntimeError('upstream secret')))
    asyncio.run(engine.scan_once())
    with factory() as db:
        row = db.get(PriceAlertHealth, rule_id)
        assert row.status == 'source_failed' and row.consecutive_failures == 1 and row.last_success_at is None
    monkeypatch.setattr(engine, '_fetch_quotes_map', AsyncMock(return_value={('CN','600519'):{'volume':5}}))
    asyncio.run(engine.scan_once())
    with factory() as db: assert db.get(PriceAlertHealth, rule_id).status == 'incomplete_data'
    monkeypatch.setattr(engine, '_fetch_quotes_map', AsyncMock(return_value={('CN','600519'):{'current_price':4}}))
    asyncio.run(engine.scan_once())
    with factory() as db:
        row = db.get(PriceAlertHealth, rule_id)
        assert row.status == 'not_matched' and row.consecutive_failures == 0 and row.last_success_at


def test_quota_gating_is_visible_and_does_not_fetch(factory, monkeypatch):
    rule_id = seed(factory)
    with factory() as db:
        rule = db.get(PriceAlertRule, rule_id); rule.max_triggers_per_day = 1; rule.trigger_count_today = 1
        rule.trigger_date = price_alert_engine._day_key(price_alert_engine._utc_now(), 'CN'); db.commit()
    engine = price_alert_engine.PriceAlertEngine()
    fetch = AsyncMock(return_value={}); monkeypatch.setattr(engine, '_fetch_quotes_map', fetch)
    result = asyncio.run(engine.scan_once())
    assert result['items'][0]['reason'] == 'daily_limit' and fetch.call_args.args[0] == []
    with factory() as db:
        item = monitoring_health(db)['items'][0]
        assert item['status'] == 'daily_limit' and item['quota']['exhausted'] and item['last_success_at'] is None


def test_delay_and_deleted_rules_cleanup(factory):
    rule_id = seed(factory); hit(factory, rule_id)
    with factory() as db:
        db.add(PriceAlertHealth(rule_id=rule_id, status='not_matched', next_scan_at=now_utc()-timedelta(minutes=4))); db.commit()
        assert monitoring_health(db)['items'][0]['status'] == 'monitoring_delayed'
        from src.modules.market.price_alert_service import delete_alert_rule
        delete_alert_rule(db, rule_id)
        assert db.query(PriceAlertDelivery).count() == db.query(PriceAlertHealth).count() == 0


def test_unknown_channel_is_not_a_success():
    from src.platform.notifications.notifier import NotifierManager
    with pytest.raises(ValueError, match='Unsupported'):
        asyncio.run(NotifierManager()._send_custom('unknown-provider', {}, 'QA', 'QA'))
