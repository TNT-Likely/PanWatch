"""Regression coverage for mixed-market holidays and all execution entry points."""
import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    AgentConfig, EntryCandidate, EntryCandidateOutcome, PaperTradingAccount,
    PaperTradingPosition, PaperTradingTrade, PriceAlertRule, Stock, StockAgent,
    StrategySignalRun, StrategyOutcome,
)
from src.platform.scheduling import trading_calendar as calendar
from src.platform.scheduling.schedule_parser import preview_schedule


def freeze(monkeypatch, instant):
    monkeypatch.setattr(calendar, "_now_in_market_tz", lambda code: instant.astimezone(calendar._market_tz(code)))


@pytest.fixture
def memory_db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def test_preview_skips_national_holiday_and_intraday_lunch():
    start = datetime(2026, 10, 3, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    runs = preview_schedule("0 9 * * 1-5", count=3, timezone="Asia/Shanghai", start=start, markets=["CN"])
    assert [run.date() for run in runs] == [date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)]
    lunch = datetime(2026, 10, 8, 11, 35, tzinfo=ZoneInfo("Asia/Shanghai"))
    intraday = preview_schedule("*/5 9-15 * * 1-5", count=2, timezone="Asia/Shanghai", start=lunch,
                                markets=["CN"], trading_hours_only=True)
    assert [(run.hour, run.minute) for run in intraday] == [(13, 0), (13, 5)]
    assert preview_schedule("0 9 * * 1-5", markets=[], start=start) == []


def test_next_us_open_is_correct_in_beijing_across_dst(monkeypatch):
    from src.modules.market.api.stocks import get_market_calendars
    for instant, expected in [
        (datetime(2026, 10, 3, 8, tzinfo=ZoneInfo("Asia/Shanghai")), "2026-10-05T21:30:00+08:00"),
        (datetime(2026, 11, 7, 8, tzinfo=ZoneInfo("Asia/Shanghai")), "2026-11-09T22:30:00+08:00"),
    ]:
        freeze(monkeypatch, instant)
        data = get_market_calendars(days=14, timezone="Asia/Shanghai")
        assert {market["market"] for market in data["markets"]} == {"CN", "HK", "US"}
        assert all(market["days"][0]["date"] == instant.date().isoformat() for market in data["markets"])
        us = next(market for market in data["markets"] if market["market"] == "US")
        assert datetime.fromisoformat(us["next_open"]).astimezone(ZoneInfo("Asia/Shanghai")).isoformat() == expected


def test_batch_agent_filters_watchlist_and_portfolio_before_analysis(monkeypatch):
    from src.modules.automation.agent_scheduler import AgentScheduler
    from src.modules.automation.base import AgentContext, PortfolioInfo, AccountInfo, PositionInfo
    from src.platform.runtime.config import AppConfig, Settings, StockConfig
    from src.platform.marketdata.models import MarketCode
    freeze(monkeypatch, datetime(2026, 10, 1, 22, tzinfo=ZoneInfo("Asia/Shanghai")))
    cn = StockConfig(symbol="601238", name="CN", market=MarketCode.CN)
    us = StockConfig(symbol="AAPL", name="US", market=MarketCode.US)
    positions = [PositionInfo(account_id=1, account_name="Account", stock_id=i, symbol=stock.symbol,
                name=stock.name, market=stock.market, cost_price=10, quantity=100)
                for i, stock in enumerate([cn, us])]
    context = AgentContext(ai_client=Mock(), notifier=Mock(), config=AppConfig(settings=Settings(), watchlist=[cn, us]),
                           portfolio=PortfolioInfo([AccountInfo(1, "Account", 0, positions)]))
    agent = SimpleNamespace(display_name="Daily report", run=AsyncMock(return_value=SimpleNamespace(raw_data={}, content="Report")))
    scheduler = AgentScheduler()
    scheduler.agents["daily_report"] = agent
    scheduler.set_context_builder(lambda name: context)
    record = Mock()
    monkeypatch.setattr("src.modules.automation.agent_scheduler.record_agent_run", record)
    asyncio.run(scheduler._run_agent("daily_report"))
    scoped = agent.run.call_args.args[0]
    assert [stock.market for stock in scoped.watchlist] == [MarketCode.US]
    assert [pos.market for pos in scoped.portfolio.all_positions] == [MarketCode.US]
    assert len(context.watchlist) == len(context.portfolio.all_positions) == 2
    # A pure CN batch must stop before collect/AI and must not produce an inbox event.
    agent.run.reset_mock()
    record.reset_mock()
    asyncio.run(scheduler._run_agent("daily_report", stock_keys=(("CN", "601238"),)))
    agent.run.assert_not_awaited()
    record.assert_not_called()


def test_effective_stock_override_is_registered_without_global_duplicate(monkeypatch, memory_db):
    import server
    from src.modules.automation.scheduling_policy import schedule_plans
    with memory_db() as db:
        cfg = AgentConfig(name="intraday_monitor", display_name="Monitor", enabled=True,
                          schedule="*/5 9-15 * * 1-5", execution_mode="single")
        inherited = Stock(symbol="601238", name="CN", market="CN")
        custom = Stock(symbol="AAPL", name="US", market="US")
        db.add_all([cfg, inherited, custom]); db.flush()
        db.add_all([StockAgent(stock_id=inherited.id, agent_name=cfg.name),
                    StockAgent(stock_id=custom.id, agent_name=cfg.name, schedule="*/10 21-23 * * 1-5")])
        db.commit()
        plans = schedule_plans(db, cfg)
        assert len(plans) == 2
        assert plans[0].stock_keys == (("CN", "601238"),)
        assert plans[1].stock_keys == (("US", "AAPL"),)
        assert plans[1].stock_agent_id is not None
    monkeypatch.setattr(server, "SessionLocal", memory_db)
    scheduler = server.build_scheduler()
    jobs = scheduler.scheduler.get_jobs()
    assert len(jobs) == 2
    assert {job.args[1] for job in jobs} == {plan.stock_keys for plan in plans}


def test_paper_scan_does_not_buy_or_sell_closed_markets_when_us_is_open(monkeypatch, memory_db):
    from src.modules.paper_trading import paper_trading_engine as module
    freeze(monkeypatch, datetime(2026, 10, 1, 22, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(module, "SessionLocal", memory_db)
    with memory_db() as db:
        db.add(PaperTradingAccount(enabled=True, initial_capital=1_000_000, current_capital=1_000_000,
                                  market_allocations={"CN": .5, "HK": 0, "US": .5}))
        for market, symbol in [("CN", "601238"), ("US", "AAPL")]:
            db.add(StrategySignalRun(strategy_code="trend_follow", snapshot_date="2026-09-30",
                stock_symbol=symbol, stock_market=market, status="active", action="buy",
                entry_low=9, entry_high=11, rank_score=90))
        db.add(PaperTradingPosition(stock_symbol="600519", stock_market="CN", quantity=100,
            entry_price=15, current_price=10, stop_loss=12, status="open",
            opened_at=datetime(2026, 9, 1)))
        db.commit()
    engine = module.PaperTradingEngine()
    quote_requests = []
    def quotes(pairs):
        quote_requests.extend(pairs)
        return {(market, symbol): {"current_price": 10} for symbol, market in pairs}
    monkeypatch.setattr(engine, "_fetch_quotes_map", quotes)
    result = engine._scan_sync()
    assert result["status"] == "ok"
    assert result["opened"] == 1 and result["closed"] == 0
    assert quote_requests and all(market == "US" for _, market in quote_requests)
    with memory_db() as db:
        assert db.query(PaperTradingPosition).filter_by(stock_symbol="600519").one().status == "open"
        assert db.query(PaperTradingTrade).count() == 0
    # Manual close is also a fill and cannot use a last-known holiday price.
    fetch = Mock(side_effect=AssertionError("closed-market quote must not be requested"))
    monkeypatch.setattr(module, "md_quote_rows", fetch)
    with memory_db() as db:
        position = db.query(PaperTradingPosition).filter_by(stock_symbol="600519").one()
        assert engine.close_position_manual(position.id)["ok"] is False
    fetch.assert_not_called()


def test_alerts_filter_before_quotes_and_manual_non_dry_scan_cannot_bypass(monkeypatch, memory_db):
    from src.modules.market import price_alert_engine as module
    freeze(monkeypatch, datetime(2026, 10, 1, 22, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(module, "SessionLocal", memory_db)
    with memory_db() as db:
        stock = Stock(symbol="601238", name="CN", market="CN")
        db.add(stock); db.flush()
        db.add(PriceAlertRule(stock_id=stock.id, enabled=True, name="Alert", market_hours_mode="always",
               condition_group={"op": "and", "items": [{"type": "price", "op": ">", "value": 1}]}))
        db.commit()
    engine = module.PriceAlertEngine()
    fetch = AsyncMock(return_value={})
    monkeypatch.setattr(engine, "_fetch_quotes_map", fetch)
    result = asyncio.run(engine.scan_once(bypass_market_hours=True, dry_run=False))
    assert result["triggered"] == 0
    assert result["items"][0]["reason"] == "non_trading_day"
    assert fetch.call_args.args[0] == []


def test_opportunity_refresh_propagates_only_eligible_markets(monkeypatch):
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler
    freeze(monkeypatch, datetime(2026, 10, 1, 22, tzinfo=ZoneInfo("Asia/Shanghai")))
    refresh = Mock(return_value={"count": 0})
    monkeypatch.setattr("src.modules.research.context_scheduler.refresh_strategy_signals", refresh)
    asyncio.run(ContextMaintenanceScheduler()._refresh_opportunities_job())
    assert refresh.call_args.kwargs["markets"] == ["US"]


def test_outcome_waits_for_actual_completed_bars_and_ignores_intraday_tail(monkeypatch):
    from src.platform.marketdata.outcome_prices import completed_outcome_bar
    rows = [SimpleNamespace(date="2026-09-30", close=100), SimpleNamespace(date="2026-10-08", close=110)]
    freeze(monkeypatch, datetime(2026, 10, 1, 22, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert completed_outcome_bar(rows[:1], date(2026, 9, 30), 1, "CN") is None
    freeze(monkeypatch, datetime(2026, 10, 8, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert completed_outcome_bar(rows, date(2026, 9, 30), 1, "CN") is None
    freeze(monkeypatch, datetime(2026, 10, 8, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert completed_outcome_bar(rows, date(2026, 9, 30), 1, "CN") == (date(2026, 10, 8), 110)
    assert completed_outcome_bar(rows, date(2026, 9, 30), 3, "CN") is None


def test_automatic_ta_linkage_does_not_escape_a_manual_holiday_analysis(monkeypatch):
    from src.modules.automation.tradingagents import operations
    freeze(monkeypatch, datetime(2026, 10, 1, 22, tzinfo=ZoneInfo("Asia/Shanghai")))
    trigger = Mock()
    monkeypatch.setattr(operations, "fire_and_forget_trigger", trigger)
    assert operations.try_auto_trigger(SimpleNamespace(symbol="601238", market="CN", change_pct=10)) is None
    trigger.assert_not_called()


def test_half_day_summary_runs_only_at_actual_market_close_plus_30(monkeypatch):
    from src.modules.paper_trading.paper_trading_scheduler import PaperTradingScheduler
    summary = AsyncMock()
    monkeypatch.setattr("src.modules.paper_trading.paper_trading_notifier.send_daily_summary", summary)
    scheduler = PaperTradingScheduler()
    freeze(monkeypatch, datetime(2026, 12, 24, 12, 30, tzinfo=ZoneInfo("Asia/Hong_Kong")))
    asyncio.run(scheduler._summary_job("HK"))
    summary.assert_awaited_once_with(markets=["HK"])
    freeze(monkeypatch, datetime(2026, 12, 24, 16, 30, tzinfo=ZoneInfo("Asia/Hong_Kong")))
    asyncio.run(scheduler._summary_job("HK"))
    assert summary.await_count == 1


def test_alert_daily_limit_uses_market_date_not_utc_midnight(monkeypatch):
    from src.modules.market.price_alert_engine import PriceAlertEngine
    freeze(monkeypatch, datetime(2026, 10, 3, 8, tzinfo=ZoneInfo("Asia/Shanghai")))
    rule = SimpleNamespace(enabled=True, expire_at=None, stock=SimpleNamespace(market="US"),
        market_hours_mode="always", trigger_date="2026-10-02", trigger_count_today=1,
        max_triggers_per_day=1, repeat_mode="repeat", last_trigger_at=None)
    # NY Friday remains Friday after UTC has crossed midnight.
    now = datetime(2026, 10, 3, 0, 5, tzinfo=ZoneInfo("UTC"))
    assert PriceAlertEngine()._can_trigger(rule, now) == (False, "daily_limit")
    assert rule.trigger_date == "2026-10-02"


def test_paper_fill_rejects_a_known_previous_day_quote(monkeypatch, memory_db):
    from src.modules.paper_trading import paper_trading_engine as module
    freeze(monkeypatch, datetime(2026, 10, 8, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(module, "SessionLocal", memory_db)
    with memory_db() as db:
        db.add(PaperTradingAccount(enabled=True, initial_capital=1_000_000, current_capital=1_000_000,
                                  market_allocations={"CN": 1, "HK": 0, "US": 0}))
        db.add(StrategySignalRun(strategy_code="trend_follow", snapshot_date="2026-09-30",
            stock_symbol="601238", stock_market="CN", status="active", action="buy",
            entry_low=9, entry_high=11, rank_score=90))
        db.add(PaperTradingPosition(stock_symbol="600519", stock_market="CN", quantity=100,
            entry_price=15, current_price=10, stop_loss=12, status="open"))
        db.commit()
    engine = module.PaperTradingEngine()
    monkeypatch.setattr(engine, "_fetch_quotes_map", lambda pairs: {
        (market, symbol): {"current_price": 10, "quote_date": "2026-09-30"} for symbol, market in pairs
    })
    result = engine._scan_sync()
    assert result["opened"] == result["closed"] == 0
    monkeypatch.setattr(module, "md_quote_rows", lambda *args: [{"current_price": 10, "quote_date": "2026-09-30"}])
    with memory_db() as db:
        position = db.query(PaperTradingPosition).one()
        assert engine.close_position_manual(position.id)["ok"] is False
