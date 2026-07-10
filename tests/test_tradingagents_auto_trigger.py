"""TradingAgents 联动触发单测。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.tradingagents import auto_trigger


def _make_agent(raw_config: dict):
    agent = MagicMock()
    agent.config = raw_config
    return agent


def test_no_change_pct_skips():
    """涨跌幅缺失 → 不触发"""
    ok, reason = auto_trigger.should_auto_trigger("601238", None)
    assert ok is False
    assert "无涨跌幅" in reason


def test_disabled_in_config_skips():
    """auto_trigger.enabled=false → 不触发"""
    with patch("src.agents.tradingagents.auto_trigger.SessionLocal") as session_factory:
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": False, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False
    assert "未启用" in reason


def test_no_agent_config_skips():
    """tradingagents agent 未注册 → 不触发"""
    with patch("src.agents.tradingagents.auto_trigger.SessionLocal") as session_factory:
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = None
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False


def test_below_threshold_skips():
    """涨跌幅低于阈值 → 不触发"""
    with patch("src.agents.tradingagents.auto_trigger.SessionLocal") as session_factory:
        db = MagicMock()
        session_factory.return_value = db
        # query(AgentConfig) 第一次返回 agent
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 3.0)
    assert ok is False
    assert "未达阈值" in reason


def test_above_threshold_within_cooldown_skips():
    """达阈值但 24h 内已触发 → 不触发"""
    with patch("src.agents.tradingagents.auto_trigger.SessionLocal") as session_factory, \
         patch("src.agents.tradingagents.auto_trigger._within_cooldown", return_value=True), \
         patch("src.agents.tradingagents.auto_trigger._budget_allows", return_value=True):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0, "cooldown_hours": 24}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False
    assert "冷却" in reason


def test_above_threshold_budget_exceeded_skips():
    """达阈值但月度预算已用完 → 不触发"""
    with patch("src.agents.tradingagents.auto_trigger.SessionLocal") as session_factory, \
         patch("src.agents.tradingagents.auto_trigger._within_cooldown", return_value=False), \
         patch("src.agents.tradingagents.auto_trigger._budget_allows", return_value=False):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False
    assert "预算" in reason


def test_above_threshold_all_pass_triggers():
    """达阈值 + 不在冷却 + 预算足 → 触发"""
    with patch("src.agents.tradingagents.auto_trigger.SessionLocal") as session_factory, \
         patch("src.agents.tradingagents.auto_trigger._within_cooldown", return_value=False), \
         patch("src.agents.tradingagents.auto_trigger._budget_allows", return_value=True):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is True
    assert "达阈值" in reason


def test_negative_change_pct_uses_abs():
    """跌 8% 也应该触发(用 |change_pct|)"""
    with patch("src.agents.tradingagents.auto_trigger.SessionLocal") as session_factory, \
         patch("src.agents.tradingagents.auto_trigger._within_cooldown", return_value=False), \
         patch("src.agents.tradingagents.auto_trigger._budget_allows", return_value=True):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, _ = auto_trigger.should_auto_trigger("601238", -8.0)
    assert ok is True


def test_try_auto_trigger_returns_none_when_disabled():
    """try_auto_trigger 在不满足条件时返回 None"""
    stock = MagicMock()
    stock.symbol = "601238"
    stock.change_pct = 8.0
    with patch("src.agents.tradingagents.auto_trigger.should_auto_trigger", return_value=(False, "test")):
        result = auto_trigger.try_auto_trigger(stock)
    assert result is None


def test_try_auto_trigger_fires_when_should():
    """try_auto_trigger 在满足条件时调 fire_and_forget_trigger"""
    stock = MagicMock()
    stock.symbol = "601238"
    stock.change_pct = 8.0
    with patch("src.agents.tradingagents.auto_trigger.should_auto_trigger", return_value=(True, "test")), \
         patch("src.agents.tradingagents.auto_trigger.fire_and_forget_trigger", return_value="trace-abc") as fire:
        result = auto_trigger.try_auto_trigger(stock)
    assert result == "trace-abc"
    fire.assert_called_once()
