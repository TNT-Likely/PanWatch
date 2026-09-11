"""trigger API 幂等性 + 状态机兜底测试。

3 条原则:
1. 任务重启后(stale),允许重新分析触发
2. 每次触发先检查"是否有真正在跑的任务",有则返回现有 trace_id 不启新任务
3. force_refresh=true 时无条件启新任务(允许"忽略缓存重新分析")
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.modules.automation import find_active_tradingagents_trace


def _fake_log(timestamp, trace_id="man-tradingagents-601127-1234"):
    le = MagicMock()
    le.id = 1
    le.timestamp = timestamp
    le.event = "ta_progress"
    le.trace_id = trace_id
    le.agent_name = "tradingagents"
    return le


def _fake_run(status: str):
    r = MagicMock()
    r.status = status
    return r


def _setup_db(latest_log, run=None):
    """构造 db.query(LogEntry) → latest_log, db.query(AgentRun) → run 的 mock"""
    db = MagicMock()
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.first.return_value = latest_log
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = run
    db.query.side_effect = [log_query, run_query]
    return db


def test_no_running_task_returns_none():
    """没日志 = 没在跑 → 允许新触发"""
    db = _setup_db(latest_log=None)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_recent_log_no_run_returns_trace():
    """最近 1 分钟有日志且无 AgentRun → 在跑,返回 trace_id"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    db = _setup_db(latest_log=log, run=None)
    assert find_active_tradingagents_trace(db, "601127") == log.trace_id


def test_stale_log_returns_none():
    """5 分钟无新进度 → stale → 允许新触发(返回 None)"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(minutes=10))
    db = _setup_db(latest_log=log, run=None)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_completed_success_returns_none():
    """AgentRun.status=success → 不在跑(允许新触发,例如重新分析)"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    run = _fake_run("success")
    db = _setup_db(latest_log=log, run=run)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_completed_failed_returns_none():
    """AgentRun.status=failed → 不在跑(允许重新分析)"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    run = _fake_run("failed")
    db = _setup_db(latest_log=log, run=run)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_running_status_returns_trace():
    """AgentRun.status=running 且日志新 → 在跑"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    run = _fake_run("running")
    db = _setup_db(latest_log=log, run=run)
    assert find_active_tradingagents_trace(db, "601127") == log.trace_id


def test_running_status_but_stale_returns_none():
    """AgentRun.status=running 但日志已过 5 分钟 → 视为 stale 不在跑"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(minutes=10))
    run = _fake_run("running")
    db = _setup_db(latest_log=log, run=run)
    # 注意:当前实现 run.status in ('success','failed') 才提前 return,
    # 'running' 但 stale 应该按 stale 处理 → None
    assert find_active_tradingagents_trace(db, "601127") is None
