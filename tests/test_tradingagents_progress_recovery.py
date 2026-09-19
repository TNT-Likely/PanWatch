"""TradingAgents 运行生命周期与采集阶段回归测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _query_chain(value):
    query = MagicMock()
    query.filter.return_value.order_by.return_value.first.return_value = value
    return query


def _db(active_run=None, latest_log=None):
    db = MagicMock()
    db.query.side_effect = [_query_chain(active_run), _query_chain(latest_log)]
    return db


def _run(status="running", created_at=None):
    run = MagicMock()
    run.status = status
    run.trace_id = "man-tradingagents-AAPL-123"
    run.created_at = created_at or datetime.now(timezone.utc)
    run.agent_name = "tradingagents"
    run.result = ""
    run.error = ""
    run.duration_ms = 0
    run.model_label = ""
    run.notify_sent = False
    return run


def test_running_agent_run_is_active_without_progress_log():
    from src.modules.automation.agent_runs import find_active_tradingagents_trace

    trace_id = find_active_tradingagents_trace(
        _db(_run(created_at=datetime.now(timezone.utc) - timedelta(seconds=30))),
        "AAPL",
    )

    assert trace_id == "man-tradingagents-AAPL-123"


def test_progress_uses_running_agent_run_when_logs_are_empty():
    from src.modules.automation.api.agents import get_run_progress

    run = _run(created_at=datetime.now(timezone.utc) - timedelta(seconds=42))
    db = MagicMock()
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = run
    db.query.side_effect = [log_query, run_query]

    with patch(
        "src.modules.automation.tradingagents.progress.aggregate_progress",
        return_value={"stages": []},
    ):
        result = get_run_progress("man-tradingagents-AAPL-123", db)

    assert result["status"] == "running"
    assert result["elapsed_sec"] >= 40
    assert result["run"]["status"] == "running"


def test_running_agent_run_is_stale_after_lifecycle_timeout():
    from src.modules.automation.agent_runs import find_active_tradingagents_trace

    old_run = _run(created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    assert find_active_tradingagents_trace(_db(old_run), "AAPL") is None


def test_progress_marks_expired_running_agent_run_stale():
    from src.modules.automation.api.agents import get_run_progress

    run = _run(created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    db = MagicMock()
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = run
    db.query.side_effect = [log_query, run_query]

    with patch(
        "src.modules.automation.tradingagents.progress.aggregate_progress",
        return_value={"stages": []},
    ):
        result = get_run_progress(run.trace_id, db)

    assert result["status"] == "stale"


def test_record_agent_run_updates_existing_running_row():
    from src.modules.automation import agent_runs

    existing = _run()
    db = MagicMock()
    running_query = MagicMock()
    running_query.filter.return_value.order_by.return_value.first.return_value = existing
    db.query.return_value = running_query

    with patch.object(agent_runs, "SessionLocal", return_value=db):
        agent_runs.record_agent_run(
            agent_name="tradingagents",
            status="success",
            result="done",
            trace_id=existing.trace_id,
            duration_ms=123,
        )

    assert existing.status == "success"
    assert existing.result == "done"
    assert existing.duration_ms == 123
    db.add.assert_not_called()


def test_start_agent_run_persists_running_state():
    from src.modules.automation import agent_runs

    db = MagicMock()
    query = MagicMock()
    query.filter.return_value.order_by.return_value.first.return_value = None
    db.query.return_value = query

    with patch.object(agent_runs, "SessionLocal", return_value=db):
        agent_runs.start_agent_run(
            agent_name="tradingagents",
            trace_id="man-tradingagents-AAPL-123",
            trigger_source="manual",
        )

    db.add.assert_called_once()
    assert db.add.call_args.args[0].status == "running"


def test_data_collection_is_a_visible_progress_stage():
    from src.modules.automation.tradingagents.progress import STAGES_ORDER, aggregate_progress

    assert STAGES_ORDER[0] == "data_collection"
    result = aggregate_progress([
        {
            "timestamp": "2026-09-19T10:00:00+00:00",
            "tags": {
                "stage": "data_collection",
                "action": "stage_start",
                "elapsed_sec": 0.1,
            },
        },
    ])
    assert result["current_stage"] == "data_collection"
    assert result["stages"][0]["status"] == "running"


def test_data_collection_source_error_is_visible_in_progress_snapshot():
    from src.modules.automation.tradingagents.progress import aggregate_progress

    result = aggregate_progress([
        {
            "timestamp": "2026-09-19T10:00:00+00:00",
            "tags": {
                "stage": "data_collection",
                "action": "source_start",
                "source": "quote",
            },
        },
        {
            "timestamp": "2026-09-19T10:00:01+00:00",
            "tags": {
                "stage": "data_collection",
                "action": "source_error",
                "source": "quote",
                "error": "Yahoo 429",
            },
        },
    ])

    assert result["data_sources"] == [
        {"name": "quote", "status": "error", "error": "Yahoo 429"},
    ]


def test_one_market_source_failure_does_not_zero_other_sources():
    import asyncio

    from src.modules.automation.tradingagents.agent import TradingAgentsAgent
    from src.modules.automation.tradingagents import agent as agent_module

    async def _run():
        agent = TradingAgentsAgent(collection_timeout_seconds=5)
        stock = MagicMock(symbol="AAPL", name="Apple")
        stock.market.value = "US"
        context = MagicMock()
        context.watchlist = [stock]
        context._trace_id = "man-tradingagents-AAPL-123"

        market_data = MagicMock()
        market_data.quotes.side_effect = RuntimeError("Yahoo 429")
        market_data.klines.return_value = ["bar"]
        market_data.capital_flow.return_value = "flow"
        market_data.events.return_value = ["event"]

        collector = MagicMock()
        collector.return_value.get_technical_indicators.return_value = {"rsi": 52}
        with (
            patch.object(agent_module, "get_market_data", return_value=market_data),
            patch(
                "src.platform.marketdata.collectors.kline_collector.KlineCollector",
                collector,
            ),
        ):
            result = await agent.collect(context)

        assert result["quote"] == {}
        assert result["klines"] == ["bar"]
        assert result["capital_flow"] == ["flow"]
        assert result["events"] == ["event"]
        assert result["technical"] == {"rsi": 52}

    asyncio.run(_run())
