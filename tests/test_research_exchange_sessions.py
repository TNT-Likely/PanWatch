"""Exchange-local scheduling and cross-midnight attribution for session research."""
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pan_investment_research import Recommendation, ResearchInput, ResearchResult, Security
from src.modules.automation.daily_report import DailyReportAgent
from src.modules.automation.scheduling_policy import market_allowed, schedule_phase
from src.modules.research.orchestration import service
from src.modules.research.orchestration.evidence import prior_plan
from src.modules.research.orchestration.models import ResearchDelivery, ResearchRun
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.models import AgentContextRun, AgentPredictionOutcome, AnalysisHistory, StockSuggestion
from src.platform.scheduling.schedule_parser import preview_schedule
from test_investment_research_integration import host, make_context


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
