"""Regression coverage for locale-aware system output outside React surfaces."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import AppSettings, NotifyChannel, PriceAlertRule, Stock


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


def test_price_alert_notification_follows_interface_language(monkeypatch):
    from src.modules.market import price_alert_engine

    sent: list[tuple[str, str]] = []

    class FakeNotifier:
        def add_channel(self, *_args, **_kwargs):
            return None

        async def notify_with_result(self, title, content):
            sent.append((title, content))
            return {"success": True}

    monkeypatch.setattr(price_alert_engine, "NotifierManager", FakeNotifier)
    engine, session = _session()
    try:
        session.add(AppSettings(key="ui_language", value="en-US"))
        stock = Stock(symbol="AAPL", name="Apple", market="US")
        channel = NotifyChannel(name="test", type="json", config={}, enabled=True, is_default=True)
        session.add_all([stock, channel])
        session.flush()
        rule = PriceAlertRule(stock_id=stock.id, name="Breakout", notify_channel_ids=[channel.id])
        session.add(rule)
        session.commit()

        ok, error = asyncio.run(
            price_alert_engine.PriceAlertEngine()._send_notify(
                session,
                rule,
                {
                    "quote": {"current_price": 200, "change_pct": 2.5},
                    "conditions": [{"matched": True, "type": "price", "op": ">=", "target": 200, "actual": 200}],
                },
            )
        )

        assert (ok, error) == (True, "")
        assert sent == [
            (
                "[Price alert] Apple (AAPL)",
                "Rule: Breakout\nCurrent price: 200.00\nChange: +2.50%\nMatched conditions:\n- price >= 200 (current: 200)",
            )
        ]
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
