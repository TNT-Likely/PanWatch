"""Idle scheduler polls must not become completed reports or inbox events."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.modules.automation import agent_scheduler, scheduling_policy
from src.platform.marketdata.models import MarketCode


def scheduler_fixture(monkeypatch, *, mode="single", markets=(), result=None):
    context = SimpleNamespace(
        model_label="test", report_language="zh-CN", suppress_notify=False,
        watchlist=[SimpleNamespace(symbol=f"STOCK-{i}", market=market)
                   for i, market in enumerate(markets)],
        notifier=SimpleNamespace(notify_with_result=AsyncMock()),
    )
    agent = SimpleNamespace(
        display_name="Intraday monitor", run=AsyncMock(return_value=result),
        run_single=AsyncMock(return_value=result),
    )
    scheduler = agent_scheduler.AgentScheduler()
    scheduler.agents["intraday_monitor"] = agent
    scheduler.execution_modes["intraday_monitor"] = mode
    scheduler.set_context_builder(lambda name: context)
    monkeypatch.setattr(scheduling_policy, "MARKETS", {
        MarketCode.CN: SimpleNamespace(name="CN", is_trading_time=lambda dt=None: False),
        MarketCode.HK: SimpleNamespace(name="HK", is_trading_time=lambda dt=None: True),
    })
    record = Mock()
    monkeypatch.setattr(agent_scheduler, "record_agent_run", record)
    return scheduler, agent, context, record


@pytest.mark.parametrize("mode", ["single", "batch"])
def test_empty_intraday_watchlist_does_not_execute_or_record_report(monkeypatch, mode):
    scheduler, agent, _, record = scheduler_fixture(monkeypatch, mode=mode)
    asyncio.run(scheduler._run_agent("intraday_monitor"))
    agent.run.assert_not_awaited()
    agent.run_single.assert_not_awaited()
    record.assert_not_called()


def test_all_closed_markets_do_not_execute_or_record_report(monkeypatch):
    scheduler, agent, _, record = scheduler_fixture(
        monkeypatch, markets=[MarketCode.CN, MarketCode.CN],
    )
    asyncio.run(scheduler._run_agent("intraday_monitor"))
    agent.run_single.assert_not_awaited()
    record.assert_not_called()


@pytest.mark.parametrize("result", [None, SimpleNamespace(raw_data={"skipped": True})])
def test_single_no_result_is_skipped_instead_of_counted_as_executed(monkeypatch, result):
    scheduler, agent, _, record = scheduler_fixture(
        monkeypatch, markets=[MarketCode.HK], result=result,
    )
    asyncio.run(scheduler._run_agent("intraday_monitor"))
    agent.run_single.assert_awaited_once()
    record.assert_not_called()


def test_mixed_poll_only_counts_actual_reports_and_keeps_delivery_failure(monkeypatch):
    report = SimpleNamespace(raw_data={"notified": False, "notify_error": "Delivery failed"})
    scheduler, agent, _, record = scheduler_fixture(
        monkeypatch, markets=[MarketCode.CN, MarketCode.HK, MarketCode.HK],
    )
    agent.run_single.side_effect = [None, report]
    asyncio.run(scheduler._run_agent("intraday_monitor"))
    assert agent.run_single.await_count == 2
    record.assert_called_once()
    values = record.call_args.kwargs
    assert values["status"] == "success"
    assert values["result"] == "single mode executed 1, skipped 2, total 3"
    assert values["notify_attempted"] is True and values["notify_sent"] is False


@pytest.mark.parametrize("with_report", [False, True])
def test_execution_failures_are_still_recorded_even_without_reports(monkeypatch, with_report):
    scheduler, agent, _, record = scheduler_fixture(
        monkeypatch, markets=[MarketCode.HK, MarketCode.HK],
    )
    report = SimpleNamespace(raw_data={}) if with_report else None
    agent.run_single.side_effect = [report, RuntimeError("Analysis failed")]
    asyncio.run(scheduler._run_agent("intraday_monitor"))
    record.assert_called_once()
    assert record.call_args.kwargs["status"] == "failed"
    assert "STOCK-1:" in record.call_args.kwargs["error"]


def test_context_failure_is_still_recorded(monkeypatch):
    scheduler, _, _, record = scheduler_fixture(monkeypatch)
    scheduler.set_context_builder(Mock(side_effect=RuntimeError("Context failed")))
    asyncio.run(scheduler._run_agent("intraday_monitor"))
    record.assert_called_once()
    assert record.call_args.kwargs["status"] == "failed"


@pytest.mark.parametrize("data", [
    {"stocks": [], "stock_data": None, "skip_reason": "市场休市"},
    {"stocks": [], "stock_data": None},
])
def test_real_intraday_batch_without_data_does_not_call_ai_or_record_report(monkeypatch, data):
    from src.modules.automation.intraday_monitor import IntradayMonitorAgent

    scheduler, _, context, record = scheduler_fixture(
        monkeypatch, mode="batch", markets=[MarketCode.HK],
    )
    context.ai_client = SimpleNamespace(chat=AsyncMock())
    agent = IntradayMonitorAgent()
    monkeypatch.setattr(agent, "collect", AsyncMock(return_value=data))
    scheduler.agents["intraday_monitor"] = agent
    asyncio.run(scheduler._run_agent("intraday_monitor"))
    context.ai_client.chat.assert_not_awaited()
    context.notifier.notify_with_result.assert_not_awaited()
    record.assert_not_called()
