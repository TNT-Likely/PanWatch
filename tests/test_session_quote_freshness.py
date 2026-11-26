import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.automation.daily_report import DailyReportAgent
from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.modules.automation.premarket_outlook import PremarketOutlookAgent
from src.modules.research.orchestration.evidence import research_date, session_quote_validity
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.models import AgentPredictionOutcome, StockSuggestion
from test_investment_research_integration import collected, host, make_context


@pytest.mark.parametrize("agent_cls", [PremarketOutlookAgent, IntradayMonitorAgent, DailyReportAgent])
def test_week_old_price_never_enters_research_or_trade_records(host, monkeypatch, agent_cls):
    engine, model = host
    agent = agent_cls()
    old = datetime.now(timezone.utc) - timedelta(days=7)

    async def stale(context):
        data = await collected(context)
        pack = data["signal_pack"]
        pack.quote = replace(pack.quote, source="test-vendor", current_price=105, change_pct=5,
                             quote_date=old.astimezone(ZoneInfo("Asia/Shanghai")).date(), source_timestamp=old)
        data["indices"] = [{"name": "stale index", "symbol": "index", "current_price": 9999, "source_timestamp": old}]
        return data

    monkeypatch.setattr(agent, "collect", stale)
    result = asyncio.run(agent.run(make_context()))
    assert result.raw_data["completion"] == "data_unavailable"
    assert result.raw_data["suggestions"] == {} and not result.raw_data["drafts"]
    assert not model.calls and "105.00" not in result.content and "stale index" not in result.content
    with Session(engine) as db:
        assert not db.scalars(select(StockSuggestion)).all()
        assert not db.scalars(select(AgentPredictionOutcome)).all()


@pytest.mark.parametrize(("phase", "observed", "source", "day", "issue"), [
    ("premarket_outlook", "2026-11-23T09:10:00+08:00", None, "2026-11-20", None),
    ("premarket_outlook", "2026-11-23T09:10:00+08:00", None, "2026-11-23", "quote_wrong_session"),
    ("daily_report", "2026-11-23T15:10:00+08:00", None, "2026-11-23", None),
    ("daily_report", "2026-11-23T15:10:00+08:00", "2026-11-23T12:00:00+08:00", "2026-11-23", "quote_before_close"),
    ("intraday_monitor", "2026-11-23T10:00:00+08:00", None, "2026-11-23", "quote_time_unknown"),
    ("intraday_monitor", "2026-11-23T10:00:00+08:00", "2026-11-23T09:55:00+08:00", "2026-11-23", None),
    ("intraday_monitor", "2026-11-23T10:00:00+08:00", "2026-11-23T09:50:00+08:00", "2026-11-23", "quote_delayed"),
    ("intraday_monitor", "2026-11-23T10:00:00+08:00", "2026-11-23T10:01:00+08:00", "2026-11-23", "quote_invalid_time"),
    ("intraday_monitor", "2026-11-23T13:01:00+08:00", "2026-11-23T11:30:00+08:00", "2026-11-23", "quote_delayed"),
    ("intraday_monitor", "2026-11-23T10:00:00+08:00", None, None, "quote_time_unknown"),
])
def test_source_time_precision_and_trading_session(phase, observed, source, day, issue):
    quote = SimpleNamespace(current_price=100, quote_date=date.fromisoformat(day) if day else None,
                            source_timestamp=datetime.fromisoformat(source) if source else None)
    ttl, reason = session_quote_validity(phase, MarketCode.CN, quote, datetime.fromisoformat(observed))
    assert reason == issue
    assert (ttl is not None) == (issue is None)


def test_manual_weekend_and_before_open_use_the_related_trading_session():
    sunday = datetime.fromisoformat("2026-11-22T12:00:00+08:00")
    assert research_date("daily_report", "CN", sunday) == date(2026, 11, 20)
    assert research_date("premarket_outlook", "CN", sunday) == date(2026, 11, 23)
    thanksgiving = datetime.fromisoformat("2026-11-26T13:00:00-05:00")
    assert research_date("daily_report", "US", thanksgiving) == date(2026, 11, 25)
    assert research_date("premarket_outlook", "US", thanksgiving) == date(2026, 11, 27)
