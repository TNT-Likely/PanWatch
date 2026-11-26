"""Exchange-local scheduling and cross-midnight attribution for session research."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pan_investment_research import Recommendation, ResearchInput, ResearchResult, Security
from src.modules.automation.daily_report import DailyReportAgent
from src.modules.automation import agent_scheduler
from src.modules.automation.premarket_outlook import PremarketOutlookAgent
from src.modules.automation.scheduling_policy import market_allowed, schedule_phase
from src.modules.research.orchestration import evidence, service
from src.modules.research.orchestration.evidence import prior_plan
from src.modules.research.orchestration.models import ResearchDelivery, ResearchRun
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.models import AgentContextRun, AgentPredictionOutcome, AnalysisHistory, StockSuggestion
from src.platform.scheduling.schedule_parser import preview_schedule
from test_investment_research_integration import collected, host, make_context


@pytest.mark.parametrize(("agent", "at", "expected"), [
    ("premarket_outlook", "2026-11-25T09:00", {"CN", "HK"}),
    ("premarket_outlook", "2026-11-25T09:30", set()),
    ("premarket_outlook", "2026-11-25T15:30", {"US"}),
    ("daily_report", "2026-11-25T12:30", {"US"}),
    ("daily_report", "2026-11-25T15:30", {"CN"}),
    ("daily_report", "2026-11-25T16:00", {"CN", "HK"}),
    ("daily_report", "2026-11-26T05:00", {"US"}),
    ("daily_report", "2026-12-24T12:00", {"HK", "US"}),
    ("daily_report", "2026-11-28T02:00", {"US"}),  # New York half-day, Shanghai Saturday
    ("daily_report", "2026-11-27T05:00", set()),  # US Thanksgiving
])
def test_scheduled_scope_matches_each_exchange_session(agent, at, expected):
    instant = datetime.fromisoformat(at).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    assert {market.value for market in MarketCode if market_allowed(agent, market, instant)} == expected


def test_preview_honors_final_close_and_does_not_treat_lunch_as_a_close():
    start = datetime(2026, 11, 25, 11, tzinfo=ZoneInfo("Asia/Shanghai"))
    runs = preview_schedule("0,30 11-16 * * 1-5", count=2, timezone="Asia/Shanghai",
                            start=start, markets=["HK"], phase=schedule_phase("daily_report"))
    assert [(run.date(), run.hour, run.minute) for run in runs] == [
        (start.date(), 16, 0), (start.date(), 16, 30)]
    pre = preview_schedule("0,30 9-10 * * 1-5", count=2, timezone="Asia/Shanghai",
                           start=start, markets=["CN"], phase=schedule_phase("premarket_outlook"))
    assert [(run.day, run.hour, run.minute) for run in pre] == [(26, 9, 0), (27, 9, 0)]


@pytest.mark.parametrize(("keys", "report_day"), [
    ([(MarketCode.US, "AAPL")], "2026-11-25"),
    ([(MarketCode.CN, "600519"), (MarketCode.US, "AAPL")], "2026-11-25"),
])
def test_manual_report_persists_each_exchange_date_once(host, monkeypatch, keys, report_day):
    engine, _ = host
    instant = datetime(2026, 11, 25, 21, 30, tzinfo=timezone.utc)
    context = make_context("man-report", keys=keys)
    context.config.settings.app_timezone = "Asia/Shanghai"
    securities = [Security(key=f"{market.value}:{symbol}", price=100) for market, symbol in keys]

    async def prepare(agent, context, run_id, **kwargs):
        context._research_gate_observations = {}
        return ResearchInput(run_id=run_id, as_of=instant, securities=securities, evidence=[]), {}

    async def run_groups(prepared, *args, **kwargs):
        return ResearchResult(run_id=prepared.run_id, status="ready", artifacts=[], recommendations=[
            Recommendation(stock_key=stock.key, action="watch", outlook="neutral", confidence=.8, reason="Test")
            for stock in prepared.securities])

    monkeypatch.setattr(service, "prepare", prepare)
    monkeypatch.setattr(service, "run_groups", run_groups)
    result = asyncio.run(DailyReportAgent().run(context))
    # Shanghai has crossed midnight. Both reviews still describe the Nov 25 close.
    expected = {market.value: "2026-11-25" for market, _ in keys}
    assert result.raw_data["market_dates"] == expected
    with Session(engine) as db:
        history = db.scalars(select(AnalysisHistory)).one()
        assert history.analysis_date == report_day and history.raw_data["market_dates"] == expected
        snapshots = db.scalars(select(AgentContextRun)).one()
        assert snapshots.analysis_date == report_day and snapshots.context_payload["market_dates"] == expected
        assert {row.stock_market: row.meta["analysis_date"] for row in db.scalars(select(StockSuggestion))} == expected
        outcomes = db.scalars(select(AgentPredictionOutcome)).all()
        assert len(outcomes) == 2 * len(keys)
        assert all(row.prediction_date == expected[row.stock_market] for row in outcomes)


def test_us_prior_plan_survives_overwritten_history_and_shanghai_midnight(host):
    engine, _ = host
    cutoff = datetime(2026, 11, 26, 5, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    morning = datetime(2026, 11, 25, 13, tzinfo=timezone.utc)
    plan = {"action": "watch", "triggers": ["Morning plan"], "reason": "Private account text"}
    with Session(engine) as db, db.begin():
        for run_id, captured, finished, status in [
            ("yesterday", morning - timedelta(days=1), morning - timedelta(days=1), "committed"),
            ("morning", morning, morning + timedelta(minutes=2), "committed"),
            ("superseded", morning, morning + timedelta(hours=1), "stale"),
            ("unavailable-at-cutoff", morning, cutoff.astimezone(timezone.utc) + timedelta(seconds=1), "committed"),
        ]:
            db.add(ResearchRun(id=run_id, trace_id=run_id, agent_name="premarket_outlook", scope_hash="scope",
                               engine="langgraph", status="completed", as_of=captured.isoformat(), finished_at=finished))
            db.add(ResearchDelivery(run_id=run_id, payload_hash=run_id, status=status, payload={"raw_data": {
                "suggestions": {"US:AAPL": plan if run_id == "morning" else {"action": "buy"}}}}))
        db.add(AnalysisHistory(agent_name="premarket_outlook", stock_symbol="*", analysis_date="2026-11-25",
            content="Other portfolio", created_at=morning, updated_at=morning, raw_data={
                "suggestions": {"HK:00700": {"action": "watch"}}}))
    assert asyncio.run(prior_plan("US:AAPL", cutoff, "Asia/Shanghai")) == {
        "analysis_date": "2026-11-25", "action": "watch", "triggers": ["Morning plan"]}
    assert asyncio.run(prior_plan("HK:AAPL", cutoff, "Asia/Shanghai")) is None
    assert asyncio.run(prior_plan("US:AAPL", morning - timedelta(minutes=1), "Asia/Shanghai")) == {
        "analysis_date": "2026-11-24", "action": "buy"}


def test_legacy_plan_on_exchange_date_rejects_next_day_and_future_edits(host):
    engine, _ = host
    morning = datetime(2026, 11, 25, 13, tzinfo=timezone.utc)
    cutoff = datetime(2026, 11, 26, 5, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    with Session(engine) as db, db.begin():
        for day, updated, action in [
            ("2026-11-25", morning, "watch"),
            ("2026-11-26", morning + timedelta(minutes=5), "buy"),
        ]:
            db.add(AnalysisHistory(agent_name="premarket_outlook", stock_symbol="*", analysis_date=day,
                content="", created_at=updated, updated_at=updated, raw_data={"suggestions": {"US:AAPL": {"action": action}}}))
    assert asyncio.run(prior_plan("US:AAPL", cutoff, "Asia/Shanghai")) == {"analysis_date": "2026-11-25", "action": "watch"}
    with Session(engine) as db, db.begin():
        db.scalars(select(AnalysisHistory).where(AnalysisHistory.analysis_date == "2026-11-25")).one().updated_at = (
            cutoff.astimezone(timezone.utc) + timedelta(seconds=1))
    assert asyncio.run(prior_plan("US:AAPL", cutoff, "Asia/Shanghai")) is None


@pytest.mark.parametrize("source", ["graph", "history"])
def test_legacy_premarket_targets_the_next_open_including_holidays(host, source):
    engine, _ = host
    ny = ZoneInfo("America/New_York")
    wednesday = datetime(2026, 11, 25, 8, 30, tzinfo=ny)
    wednesday_close = wednesday.replace(hour=16, minute=30)
    friday_close = datetime(2026, 11, 27, 13, 30, tzinfo=ny)
    with Session(engine) as db, db.begin():
        for name, when in [("morning", wednesday), ("after-close", wednesday.replace(hour=16, minute=10))]:
            at = when.astimezone(timezone.utc)
            raw = {"suggestions": {"US:AAPL": {"action": "watch", "triggers": [name]}}}
            if source == "graph":
                db.add(ResearchRun(id=name, trace_id=name, scope_hash=name, engine="langgraph", agent_name="premarket_outlook",
                    status="completed", as_of=at.isoformat(), finished_at=at))
                db.add(ResearchDelivery(run_id=name, payload_hash=name, status="committed", payload={"raw_data": raw}))
            else:
                db.add(AnalysisHistory(agent_name="premarket_outlook", stock_symbol="*",
                    analysis_date=at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                    content="", created_at=at, updated_at=at, raw_data=raw))
    for when, trigger in [(wednesday_close, "morning"), (friday_close, "after-close")]:
        assert asyncio.run(prior_plan("US:AAPL", when, "Asia/Shanghai")) == {
            "analysis_date": when.date().isoformat(), "action": "watch", "triggers": [trigger]}


@pytest.mark.parametrize(("scheduled", "all_late"), [(True, False), (True, True), (False, True)])
def test_premarket_crossing_open_keeps_scheduled_date_or_uses_manual_observation(host, monkeypatch, scheduled, all_late):
    engine, model = host
    keys = [(MarketCode.CN, "600519"), (MarketCode.HK, "00700")]
    context = make_context("sch-premarket-boundary" if scheduled else "man-premarket-boundary", keys)
    agent = PremarketOutlookAgent()
    before = datetime(2026, 11, 25, 1, 29, tzinfo=timezone.utc)
    after = before + timedelta(seconds=61)

    class Clock(datetime):
        instant = before

        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)

    async def collect(scoped):
        # Each market finishes independently; waiting for HK must not make CN's capture late.
        if all_late or scoped.watchlist[0].market == MarketCode.HK:
            Clock.instant = after
        data = await collected(scoped)
        quote = data["signal_pack"].quote
        quote.source = "scheduled-premarket-fixture"
        quote.quote_date = (before - timedelta(days=1)).date()
        quote.source_timestamp = None
        return data

    monkeypatch.setattr(agent_scheduler, "datetime", Clock)
    monkeypatch.setattr(evidence, "datetime", Clock)
    monkeypatch.setattr(agent, "collect", collect)
    record = Mock()
    monkeypatch.setattr(agent_scheduler, "record_agent_run", record)
    if scheduled:
        scheduler = agent_scheduler.AgentScheduler()
        scheduler.agents[agent.name] = agent
        scheduler.set_context_builder(lambda name: context)
        asyncio.run(scheduler._run_agent(agent.name, stock_keys=keys))
    else:
        asyncio.run(agent.run(context))
    with Session(engine) as db:
        delivery = db.scalars(select(ResearchDelivery)).one()
        result = delivery.payload["raw_data"]
        if not scheduled:
            assert result["completion"] == "complete" and "missed_premarket" not in result
            assert result["market_dates"] == {"CN": "2026-11-26", "HK": "2026-11-26"}
            return
        assert result["analysis_date"] == "2026-11-25"
        missed = {"HK:00700": "2026-11-25"}
        if all_late:
            missed["CN:600519"] = "2026-11-25"
            assert delivery.status == "skipped" and result["research_reason"] == "premarket_window_closed"
            assert not model.calls and not db.scalars(select(AgentPredictionOutcome)).all()
            record.assert_not_called()
        else:
            assert result["completion"] == "complete" and result["scope"] == ["CN:600519"]
            assert result["market_dates"] == {"CN": "2026-11-25"}
            assert set(result["suggestions"]) == {"CN:600519"}
            assert {outcome.prediction_date for outcome in db.scalars(select(AgentPredictionOutcome))} == {"2026-11-25"}
            record.assert_called_once()
        assert result["missed_premarket"] == missed


def test_legacy_plan_one_microsecond_before_open_belongs_to_that_session(host):
    engine, _ = host
    opening = datetime(2026, 11, 25, 14, 30, tzinfo=timezone.utc)
    with Session(engine) as db, db.begin():
        for name, captured in [("before", opening - timedelta(microseconds=1)), ("opening", opening)]:
            db.add(ResearchRun(id=name, trace_id=name, scope_hash=name, engine="langgraph", agent_name="premarket_outlook",
                status="completed", as_of=captured.isoformat(), finished_at=captured))
            db.add(ResearchDelivery(run_id=name, payload_hash=name, status="committed", payload={
                "raw_data": {"suggestions": {"US:AAPL": {"action": "watch", "triggers": [name]}}}}))
    plan = asyncio.run(prior_plan("US:AAPL", opening + timedelta(hours=7), "Asia/Shanghai"))
    assert plan == {"analysis_date": "2026-11-25", "action": "watch", "triggers": ["before"]}
