"""僵尸 running 检测单测 — server 重启 / 任务死掉后,前端 polling 应能被告知 stale。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _fake_log(timestamp, message="stage:market_analyst", tags=None):
    le = MagicMock()
    le.id = 1
    le.timestamp = timestamp
    le.level = "INFO"
    le.message = message
    le.tags = tags or {}
    le.event = "ta_progress"
    le.trace_id = "trace-stale-test"
    return le


def test_recent_log_status_running():
    """最近 1 分钟内有日志 → status=running"""
    from src.web.api.agents import get_run_progress

    now = datetime.now(timezone.utc)
    recent = _fake_log(now - timedelta(seconds=30))

    db = MagicMock()
    # query(LogEntry) 链 → all() 返回日志
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [recent]
    # query(AgentRun) 链 → first() 返回 None
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = None
    db.query.side_effect = [log_query, run_query]

    with patch("src.modules.automation.tradingagents.progress.aggregate_progress", return_value={"stages": []}):
        result = get_run_progress("trace-stale-test", db)
    assert result["status"] == "running"


def test_old_log_status_stale():
    """最后日志距今 > 5 分钟 → status=stale(server 重启 / 进程死掉)"""
    from src.web.api.agents import get_run_progress

    now = datetime.now(timezone.utc)
    old = _fake_log(now - timedelta(minutes=10))

    db = MagicMock()
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [old]
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = None
    db.query.side_effect = [log_query, run_query]

    with patch("src.modules.automation.tradingagents.progress.aggregate_progress", return_value={"stages": []}):
        result = get_run_progress("trace-stale-test", db)
    assert result["status"] == "stale"


def test_no_logs_status_not_found():
    """没日志没 run → status=not_found"""
    from src.web.api.agents import get_run_progress

    db = MagicMock()
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = None
    db.query.side_effect = [log_query, run_query]

    with patch("src.modules.automation.tradingagents.progress.aggregate_progress", return_value={"stages": []}):
        result = get_run_progress("trace-stale-test", db)
    assert result["status"] == "not_found"


def test_run_completed_overrides_log_status():
    """有 AgentRun 完成记录时,以 run.status 为准,不再判 stale"""
    from src.web.api.agents import get_run_progress

    now = datetime.now(timezone.utc)
    old_log = _fake_log(now - timedelta(minutes=20))

    fake_run = MagicMock()
    fake_run.agent_name = "tradingagents"
    fake_run.status = "success"
    fake_run.result = "ok"
    fake_run.error = ""
    fake_run.duration_ms = 180000
    fake_run.model_label = "deepseek-chat"
    fake_run.notify_sent = True

    db = MagicMock()
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [old_log]
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = fake_run
    db.query.side_effect = [log_query, run_query]

    with patch("src.modules.automation.tradingagents.progress.aggregate_progress", return_value={"stages": []}):
        result = get_run_progress("trace-stale-test", db)
    assert result["status"] == "success"
