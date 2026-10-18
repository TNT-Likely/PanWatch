"""Exercise the real persisted outbox → notifier → disposable HTTP receiver."""
import asyncio
from datetime import timedelta

import pytest

from test_monitoring_delivery import factory, hit, seed
from test_rest_notifications import gateway
from src.modules.market.alert_delivery import AlertDeliveryWorker, now_utc, retry_delivery
from src.platform.persistence.models import NotifyChannel, PriceAlertDelivery


@pytest.fixture(autouse=True)
def _suppress_notifications():
    # Override global no-op; these tests send only to a disposable local gateway.
    pass


def configure(factory, config):
    hit(factory, seed(factory))
    with factory() as db:
        channel = db.query(NotifyChannel).one()
        channel.type = 'custom_rest'
        channel.config = config
        db.commit()


def test_real_outbox_replay_is_stable_after_gateway_accepted_but_response_failed(factory):
    with gateway((503, 202)) as (config, calls):
        configure(factory, config)
        assert asyncio.run(AlertDeliveryWorker(factory).drain(limit=1)) == {'processed': 1}
        assert len(calls) == 1
        with factory() as db:
            row = db.query(PriceAlertDelivery).one()
            assert row.status == 'retry' and row.attempts == 1
            retry_delivery(db, row.id)
        assert asyncio.run(AlertDeliveryWorker(factory).drain(limit=1)) == {'processed': 1}
        assert len(calls) == 2 and calls[0] == calls[1]
        with factory() as db:
            row = db.query(PriceAlertDelivery).one()
            assert row.status == 'delivered' and row.attempts == 2


def test_real_outbox_auth_failure_blocks_until_operator_retry(factory):
    with gateway((401, 202)) as (config, calls):
        configure(factory, config)
        asyncio.run(AlertDeliveryWorker(factory).drain(limit=1))
        with factory() as db:
            row = db.query(PriceAlertDelivery).one()
            assert row.status == 'blocked' and row.error_code == 'channel_config_invalid'
            assert row.next_attempt_at is None
            identifier = row.id
        assert asyncio.run(AlertDeliveryWorker(factory).drain(limit=1)) == {'processed': 0}
        with factory() as db:
            retry_delivery(db, identifier)
        asyncio.run(AlertDeliveryWorker(factory).drain(limit=1))
        assert len(calls) == 2 and calls[0] == calls[1]


def test_recovered_lease_reuses_same_gateway_id(factory):
    with gateway() as (config, calls):
        configure(factory, config)
        claim = AlertDeliveryWorker(factory).claim()
        with factory() as db:
            db.get(PriceAlertDelivery, claim['id']).lease_until = now_utc() - timedelta(seconds=1)
            db.commit()
        asyncio.run(AlertDeliveryWorker(factory).drain(limit=1))
        import json
        assert json.loads(calls[0][1])['event_id'] == claim['event_id']


def test_shared_gateway_outbox_destinations_keep_persisted_ids_after_config_repair(factory, tmp_path):
    import json
    import sqlite3
    from test_rest_boundaries import durable_gateway

    with durable_gateway(tmp_path / 'gateway.db') as (origin, calls):
        rule_id = seed(factory, channels=2)
        with factory() as db:
            for channel in db.query(NotifyChannel).all():
                channel.type = 'custom_rest'
                channel.config = {'endpoint_url': f'{origin}/chat-{channel.id}'}
            db.commit()
        hit(factory, rule_id)
        with factory() as db:
            expected = {row.event_id for row in db.query(PriceAlertDelivery).all()}
        assert asyncio.run(AlertDeliveryWorker(factory).drain(limit=2)) == {'processed': 2}
        assert len(calls) == 2
        events = [json.loads(call[2]) for call in calls]
        assert {event['delivery_id'] for event in events} == expected
        assert all(event['delivery_id'] == event['event_id'] for event in events)
        with factory() as db:
            for row in db.query(PriceAlertDelivery).all():
                assert row.status == 'retry' and row.attempts == 1
                channel = db.get(NotifyChannel, row.channel_id)
                channel.config = {'endpoint_url': f'{origin}/repaired-chat-{channel.id}', 'timeout_seconds': '7'}
                db.commit()
                retry_delivery(db, row.id)
        # New worker simulates a process restart; no new identity from either
        # retry or the repaired config, even after an ambiguous first response.
        assert asyncio.run(AlertDeliveryWorker(factory).drain(limit=2)) == {'processed': 2}
        assert len(calls) == 4
        assert [call[1:] for call in calls[:2]] == [call[1:] for call in calls[2:]]
        with sqlite3.connect(tmp_path / 'gateway.db') as db:
            assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 2
        with factory() as db:
            assert all(row.status == 'delivered' and row.attempts == 2 for row in db.query(PriceAlertDelivery).all())
