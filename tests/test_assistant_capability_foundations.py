"""C01–C05 contracts: provenance, complete rules, scoped history and diagnosis."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pan_agent import ModelMessage, RunRequest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.assistant import context_tools, tools
from src.modules.assistant.result_builder import build_deterministic_assistant_result
from src.modules.assistant.watch_request import alert_capability_error, inspect_watch_request
from src.modules.market.price_alert_service import validate_condition_group, parse_expire_at
from src.platform.marketdata.quote_display import assistant_quote_fields
from src.platform.persistence.database import Base
from src.platform.persistence.models import AnalysisHistory, NotifyChannel, PriceAlertRule, Stock, StockSuggestion


@pytest.fixture
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def request(text="测试工具", context=None):
    return RunRequest(run_id="test", messages=[ModelMessage(role="user", content=text)], context=context or {})


@pytest.mark.parametrize("quote,expected", [
    ({"quote_date": "2026-10-07", "source_timestamp": "2026-10-07T15:00:00+08:00"}, "stale"),
    ({}, "unknown"),
    ({"quote_date": "2026-10-08"}, "delayed"),
    ({"source_timestamp": "2026-10-08T10:29:00+08:00"}, "fresh"),
    ({"source_timestamp": "2026-10-08T10:29:00"}, "unknown"),
    ({"source_timestamp": "2026-10-08T12:29:00+08:00"}, "unknown"),
])
def test_quote_freshness_requires_actual_source_time(monkeypatch, quote, expected):
    from src.platform.marketdata import quote_display
    monkeypatch.setattr(quote_display.calendar, "market_status", lambda *_: "trading")
    data = assistant_quote_fields("CN", {"current_price": 100, "change_pct": 9, **quote}, datetime.fromisoformat("2026-10-08T10:30:00+08:00"))
    assert data["freshness"] == expected
    assert data["change_pct"] == (9 if expected == "fresh" else None)
    assert data["source_change_pct"] == 9


def test_recent_closed_quote_is_not_live(monkeypatch):
    from src.platform.marketdata import quote_display
    monkeypatch.setattr(quote_display.calendar, "market_status", lambda *_: "closed")
    data = assistant_quote_fields("HK", {"source_timestamp": "2026-10-08T16:00:00+08:00"}, datetime.fromisoformat("2026-10-08T16:01:00+08:00"))
    assert data["freshness"] == "delayed"
    assert not data["is_realtime"]


def test_structured_request_and_write_guard_preserve_horizon_and_combination():
    now = datetime.now(UTC)
    text = "CN:600519 价格突破200且量比>2，两周有效，发飞书"
    plan = inspect_watch_request(text, now=now)
    assert plan["horizon"]["days"] == 14
    assert plan["instruments"] == [{"symbol": "600519", "market": "CN"}]
    assert plan["requested_condition_types"] == ["price", "volume_ratio"]
    req = request(text, {"watch_request": plan})
    args = {"symbol": "600519", "condition_group": {"op": "and", "items": [{"type": "price", "op": ">", "value": 200}, {"type": "volume_ratio", "op": ">", "value": 2}]}, "expire_at": plan["horizon"]["expire_at"], "notify_channel_ids": [1]}
    assert alert_capability_error(req, "create_price_alert", args) is None
    assert "horizon" in alert_capability_error(req, "create_price_alert", {**args, "expire_at": (now + timedelta(days=1)).isoformat()})
    assert alert_capability_error(req, "create_price_alert", {"direction": "above", "target_price": 200})


def test_unsupported_close_and_moving_average_are_blocked():
    req = request("CN:600519 收盘站上20日均线时提醒我")
    plan = inspect_watch_request(req.messages[0].content)
    assert set(plan["unsupported_conditions"]) >= {"bar_close_confirmation", "moving_average_trigger"}
    assert "Unsupported" in alert_capability_error(req, "create_price_alert", {"direction": "above", "target_price": 200})


@pytest.mark.parametrize("value", [None, "2", True, float("nan"), float("inf"), -1])
def test_condition_values_fail_closed(value):
    with pytest.raises(ValueError):
        validate_condition_group({"op": "and", "items": [{"type": "volume_ratio", "op": ">", "value": value}]})


def test_expiry_normalizes_timezone_to_sqlite_utc():
    assert parse_expire_at("2026-10-22T22:00:00+08:00") == datetime(2026, 10, 22, 14)


def test_complete_combination_persists_and_update_retains_unmentioned_fields(database, monkeypatch):
    db = database
    db.add_all([Stock(symbol="600519", name="QA stock", market="CN"), NotifyChannel(name="QA 飞书", type="feishu", enabled=True, config={"webhook": "private-secret"})])
    db.commit()
    channel = db.query(NotifyChannel).first()
    registry = tools.build_panwatch_tool_registry(db)
    monkeypatch.setattr(tools, "md_quote_rows", lambda *_: [{"symbol": "600519", "market": "CN", "name": "QA stock", "current_price": 100}])
    group = {"op": "and", "items": [{"type": "price", "op": ">", "value": 200}, {"type": "volume_ratio", "op": ">", "value": 2}]}
    expiry = (datetime.now(UTC) + timedelta(days=14)).isoformat()
    result = asyncio.run(registry.execute("create_price_alert", request(), {"symbol": "600519", "condition_group": group, "expire_at": expiry, "notify_channel_ids": [channel.id], "cooldown_minutes": 0, "max_triggers_per_day": 0, "repeat_mode": "once", "market_hours_mode": "always"}))
    assert result.ok
    row = db.query(PriceAlertRule).one()
    assert row.condition_group == group and row.cooldown_minutes == 0
    assert row.max_triggers_per_day == 0 and row.notify_channel_ids == [channel.id]
    assert result.data["condition_group"] == group
    updated = asyncio.run(registry.execute("update_price_alert", request(), {"rule_id": row.id, "name": "renamed"}))
    assert updated.data["condition_group"] == group
    assert updated.data["expire_at"] == result.data["expire_at"]
    channels = asyncio.run(registry.execute("get_notification_channels", request(), {}))
    assert "private-secret" not in str(channels.model_dump())
    rejected = asyncio.run(registry.execute("create_price_alert", request(), {"symbol": "600519", "condition_group": group, "notify_channel_ids": [9999]}))
    assert not rejected.ok and db.query(PriceAlertRule).count() == 1


def test_history_filters_market_and_includes_expired_original_opinion(database):
    database.add_all([
        StockSuggestion(stock_symbol="00700", stock_market="HK", action="watch", action_label="观望", reason="historical reason", agent_name="daily_report", expires_at=datetime.now(UTC).replace(tzinfo=None)-timedelta(days=2)),
        StockSuggestion(stock_symbol="00700", stock_market="CN", action="buy", action_label="买入", reason="wrong market", agent_name="daily_report"),
        AnalysisHistory(stock_symbol="00700", agent_name="legacy", analysis_date="2026-10-01", content="unscoped opinion"),
    ])
    database.commit()
    result = asyncio.run(tools.build_panwatch_tool_registry(database).execute("get_research_history", request(), {"symbol": "700", "market": "HK"}))
    assert result.data["count"] == 1
    assert result.data["items"][0]["content"] == "historical reason"
    assert result.data["items"][0]["expired"]
    assert result.data["unscoped_legacy_reports_excluded"] == 1


def test_announcement_details_verify_instrument_and_disclose_missing_body(database, monkeypatch):
    event = SimpleNamespace(source="eastmoney", external_id="AN2026100800000001", event_type="notice", title="QA 公告", publish_time=datetime(2026,10,8), symbols=["600519"], importance=1, url="https://data.eastmoney.com/notices/detail/600519/AN2026100800000001.html")
    monkeypatch.setattr(context_tools, "get_market_data", lambda: SimpleNamespace(events=lambda *a, **k: [event]))
    monkeypatch.setattr(context_tools, "fetch_announcement_fulltext", lambda *a, **k: "")
    registry = tools.build_panwatch_tool_registry(database)
    result = asyncio.run(registry.execute("get_event_details", request(), {"symbol": "600519", "external_id": event.external_id}))
    assert result.ok and not result.data["content_available"]
    assert "仅返回标题" in result.summary
    wrong = asyncio.run(registry.execute("get_event_details", request(), {"symbol": "000001", "external_id": event.external_id}))
    assert not wrong.ok and wrong.error_code == "event_not_found"


def test_nested_diagnosis_judgments_reference_source_specific_evidence():
    invocation = SimpleNamespace(tool_name="portfolio_diagnosis", call_id="diag", status="completed", summary="诊断", arguments={}, observed_at=datetime.now(UTC), source_data=[], result_data={
        "nested_tools": [{"call_id": "diag_nested_1", "tool_name": "get_kline_summary", "ok": True, "summary": "收盘100", "arguments": {"symbol": "600519", "market": "CN"}, "data": {"last_close": 100, "asof": "2026-10-07"}, "sources": [{"name": "真实K线", "as_of": "2026-10-07"}], "observed_at": datetime.now(UTC).isoformat()}],
        "diagnosis_steps": [{"id": "1", "title": "茅台趋势", "text": "趋势判断", "status": "completed", "call_ids": ["diag_nested_1"]}],
    })
    result = build_deterministic_assistant_result(task_id=1, answer="诊断结果", invocations=[invocation], language="zh-CN")
    assert result.evidence[0].tool_name == "get_kline_summary"
    assert result.evidence[0].data_at == "2026-10-07"
    assert result.judgments[0].evidence_ids == [result.evidence[0].id]


def test_fetch_time_alone_cannot_establish_external_evidence_freshness():
    invocation = SimpleNamespace(tool_name="get_stock_news", call_id="news", status="completed", summary="新闻", arguments={}, observed_at=datetime.now(UTC), source_data=[{"name":"无时间新闻"}], result_data={"items": []})
    result = build_deterministic_assistant_result(task_id=1, answer="结果", invocations=[invocation], language="zh-CN")
    assert result.evidence[0].freshness == "unknown" and result.evidence[0].freshness_basis == "unknown"
