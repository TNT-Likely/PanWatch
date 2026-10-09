"""Regression coverage for locale-aware system output outside React surfaces."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    AppSettings, NotifyChannel, PriceAlertDelivery, PriceAlertHit, PriceAlertRule, Stock,
)


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _contains_han(value: object) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in str(value))


def test_assistant_tool_specs_and_descriptors_follow_interface_language():
    from src.modules.assistant.portfolio_diagnosis import PortfolioDiagnosisExtension
    from src.modules.assistant.tool_descriptors import localized_tool_descriptors
    from src.modules.assistant.tools import build_panwatch_tool_registry

    engine, session = _session()
    try:
        session.add(AppSettings(key="ui_language", value="en-US"))
        session.commit()

        specs = build_panwatch_tool_registry(session).registered_tools()
        assert specs
        assert all(not _contains_han(json.dumps(spec.model_dump(), ensure_ascii=False)) for spec in specs)

        descriptors = localized_tool_descriptors("en-US")
        assert descriptors
        assert all(not _contains_han(descriptor.model_dump_json()) for descriptor in descriptors)

        diagnosis = PortfolioDiagnosisExtension(session, None, None)._tool_spec()
        assert diagnosis.title == "Diagnose portfolio"
        assert not _contains_han(diagnosis.description)
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("language", "expected_title", "expected_content"),
    [
        (
            "en-US",
            "[Price alert] Apple (AAPL)",
            "Rule: Breakout\nCurrent price: 200.00\nChange: +2.50%\n"
            "Matched conditions:\n- price >= 200 (current: 200)",
        ),
        (
            "zh-CN",
            "【价格提醒】Apple (AAPL)",
            "规则: Breakout\n现价: 200.00\n涨跌幅: +2.50%\n"
            "命中条件:\n- price >= 200 (当前: 200)",
        ),
    ],
)
def test_price_alert_notification_follows_interface_language(
    monkeypatch, language, expected_title, expected_content
):
    from src.modules.market import alert_delivery, price_alert_engine

    sent: list[tuple[str, str]] = []

    class FakeNotifier:
        def add_channel(self, *_args, **_kwargs):
            return None

        async def notify_with_result(self, title, content):
            sent.append((title, content))
            return {"success": True}

    monkeypatch.setattr(alert_delivery, "NotifierManager", FakeNotifier)
    monkeypatch.setattr(
        "src.platform.scheduling.trading_calendar.is_trading_day", lambda *_args: True
    )
    engine, session = _session()
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(price_alert_engine, "SessionLocal", sessions)
    try:
        language_setting = AppSettings(key="ui_language", value=language)
        session.add(language_setting)
        stock = Stock(symbol="AAPL", name="Apple", market="US")
        channel = NotifyChannel(name="test", type="json", config={}, enabled=True, is_default=True)
        session.add_all([stock, channel])
        session.flush()
        rule = PriceAlertRule(
            stock_id=stock.id, name="Breakout", notify_channel_ids=[channel.id],
            market_hours_mode="always",
        )
        session.add(rule)
        session.commit()

        hit_id = price_alert_engine.PriceAlertEngine()._persist_hit(
            rule.id,
            price_alert_engine._utc_now(),
            {
                "quote": {"current_price": 200, "change_pct": 2.5},
                "conditions": [{"matched": True, "type": "price", "op": ">=", "target": 200, "actual": 200}],
            },
            200,
        )
        assert hit_id is not None
        delivery = session.query(PriceAlertDelivery).filter_by(hit_id=hit_id).one()
        expected_payload = (
            expected_title, f"{expected_content}\n\nEvent ID: {delivery.event_id}"
        )
        assert (delivery.title, delivery.content) == expected_payload
        assert delivery.status == "pending"
        assert sent == []

        # Delivery must preserve the queued locale even if the UI language changes.
        language_setting.value = "zh-CN" if language == "en-US" else "en-US"
        session.commit()
        assert asyncio.run(alert_delivery.AlertDeliveryWorker(sessions).drain()) == {
            "processed": 1
        }
        assert sent == [expected_payload]
        session.expire_all()
        assert session.query(PriceAlertDelivery).filter_by(hit_id=hit_id).one().status == "delivered"
        assert session.get(PriceAlertHit, hit_id).notify_success
    finally:
        session.close()
        engine.dispose()


def test_intraday_notification_summary_follows_report_language():
    from src.modules.automation.intraday_monitor import IntradayMonitorAgent
    from src.platform.marketdata.models import MarketCode, StockData

    stock = StockData(
        symbol="AAPL",
        name="Apple",
        market=MarketCode.US,
        current_price=200,
        change_pct=2.5,
        change_amount=5,
        volume=1,
        turnover=1,
        open_price=195,
        high_price=201,
        low_price=194,
        prev_close=195,
    )
    content = IntradayMonitorAgent()._format_human_readable_content(
        stock,
        {"action_label": "Buy", "signal": "Breakout", "reason": "Momentum improved"},
        "{}",
        "en-US",
    )

    assert "Current price: 200.00" in content
    assert not _contains_han(content)


def test_selfcheck_test_notification_follows_report_language(monkeypatch):
    from types import SimpleNamespace

    from src.modules.administration import selfcheck
    import src.platform.notifications.notifier as notifier_module

    sent: list[tuple[str, str]] = []

    class FakeNotifier:
        def add_channel(self, *_args, **_kwargs):
            return None

        async def notify_with_result(self, title, content, **_kwargs):
            sent.append((title, content))
            return {"success": True}

    monkeypatch.setattr(notifier_module, "NotifierManager", FakeNotifier)
    result = asyncio.run(
        selfcheck.probe_notify_channel(
            SimpleNamespace(id=1, name="test", type="json", config={}),
            send=True,
            report_language="en-US",
        )
    )

    assert result["status"] == "ok"
    assert sent == [("System check", "This is a PanWatch system-check test notification.")]
