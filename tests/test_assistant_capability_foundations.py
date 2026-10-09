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


@pytest.mark.parametrize("status", ["closed", "pre_market", "after_hours", "break"])
def test_recent_closed_quote_is_not_live(monkeypatch, status):
    from src.platform.marketdata import quote_display
    monkeypatch.setattr(quote_display.calendar, "market_status", lambda *_: status)
    data = assistant_quote_fields("HK", {"source_timestamp": "2026-10-08T16:00:00+08:00"}, datetime.fromisoformat("2026-10-08T16:01:00+08:00"))
    assert data["freshness"] == "delayed"
    assert not data["is_realtime"]
    assert data["quote_semantics"] == "provider_snapshot" and data["bar_close_confirmed"] is False


def test_session_status_cannot_confirm_a_closing_price():
    invocation = SimpleNamespace(tool_name="get_stock_quote", call_id="quote", status="completed", summary="供应商快照", arguments={"symbol":"600519","market":"CN"}, observed_at=datetime.now(UTC), source_data=[], result_data={"symbol":"600519","market":"CN","current_price":100,"market_status":"break","freshness":"delayed","bar_close_confirmed":False})
    result = build_deterministic_assistant_result(task_id=1, answer="午休报价", invocations=[invocation], language="zh-CN")
    assert any("本次未验证收盘已确认" in item for item in result.missing_data)


def test_indicator_periods_and_consecutive_sessions_are_not_request_horizons():
    assert inspect_watch_request("CN:600519 收盘站上20日均线且连续3天满足时提醒")["horizon"] is None
    assert inspect_watch_request("CN:600519 连续3天满足，未来两周有效")["horizon"]["days"] == 14


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


@pytest.mark.parametrize("text,gaps", [
    ("CN:600519 收盘高于999999才提醒", {"bar_close_confirmation"}),
    ("CN:600519 连续三个交易日收盘高于20日均线才提醒", {"bar_close_confirmation", "moving_average_trigger", "consecutive_sessions"}),
    ("CN:600519 价格>999999且量比>2，或者涨跌幅>5时提醒", {"nested_condition_logic"}),
])
def test_ordinary_unsupported_wording_cannot_reach_alert_approval(text, gaps):
    plan = inspect_watch_request(text)
    assert set(plan["unsupported_conditions"]) >= gaps
    req = request(text, {"watch_request": plan})
    assert "Unsupported" in alert_capability_error(req, "create_price_alert", {"symbol": "600519", "market": "CN", "target_price": 999999})


@pytest.mark.parametrize("symbol,market", [("AAPL", "US"), ("600519", "HK"), ("000001", "CN")])
def test_explicit_alert_instrument_cannot_be_replaced(symbol, market):
    assert "instrument differs" in alert_capability_error(request("CN:600519 价格高于999999时提醒"), "create_price_alert", {"symbol": symbol, "market": market, "target_price": 999999})


def test_named_target_can_be_resolved_even_on_a_different_stock_page():
    req = request("给腾讯设置股价高于999999的提醒", {"stock_symbol":"600519", "stock_market":"CN"})
    assert alert_capability_error(req, "create_price_alert", {"symbol":"00700", "market":"HK", "target_price":999999}) is None


def test_older_checkpoint_cannot_bypass_newly_recognized_capability_gap():
    text = "CN:600519 收盘高于999999才提醒，两周有效"
    plan = inspect_watch_request(text, now=datetime(2026,10,8,tzinfo=UTC))
    plan["unsupported_conditions"] = []  # A persisted plan from the old parser.
    expiry = plan["horizon"]["expire_at"]
    req = request(text, {"watch_request":plan})
    assert "bar_close_confirmation" in alert_capability_error(req, "create_price_alert", {"symbol":"600519", "market":"CN", "target_price":999999, "expire_at":expiry})
    assert req.context["watch_request"]["horizon"]["expire_at"] == expiry


def test_channel_choice_does_not_change_condition_logic():
    assert inspect_watch_request("价格>999999且量比>2，发飞书或者邮件")["condition_logic"] == "and"
    assert inspect_watch_request("价格>999999或低于1，发飞书")["condition_logic"] == "or"
    assert inspect_watch_request("HK:700 价格高于999999时提醒")["instruments"] == [{"market": "HK", "symbol": "00700"}]
    assert alert_capability_error(request("HK:700 价格高于999999时提醒"), "create_price_alert", {"symbol": "700", "market": "HK", "target_price": 999999}) is None


def test_combination_rule_does_not_change_instrument_scope_to_portfolio():
    assert inspect_watch_request("为 CN:600519 设置组合提醒，价格>999999且量比>2")["scope"] == "instruments"
    assert inspect_watch_request("检查 CN:600519 的组合条件")["scope"] == "instruments"
    assert inspect_watch_request("看看我的组合")["scope"] == "portfolio"
    assert inspect_watch_request("读取我的自选")["scope"] == "watchlist"


def test_capability_check_keeps_unmet_requirements_in_result():
    invocation = SimpleNamespace(tool_name="check_watch_request", call_id="check", status="completed", summary="已完成能力检查", arguments={}, observed_at=datetime.now(UTC), source_data=[], result_data=inspect_watch_request("CN:600519 收盘高于20日均线连续三个交易日才提醒"))
    result = build_deterministic_assistant_result(task_id=1, answer="检查完成", invocations=[invocation], language="zh-CN")
    assert any("收盘确认" in item and "均线触发" in item and "连续交易日满足" in item and "不代表提醒已创建" in item for item in result.missing_data)


def test_unresolved_retry_cannot_report_empty_conditions_as_supported(database):
    from src.modules.assistant.watch_request import resolve_watch_request
    plan = resolve_watch_request("再试一下", [])
    req = request("再试一下", {"watch_request":plan})
    checked = asyncio.run(tools.build_panwatch_tool_registry(database).execute("check_watch_request", req, {}))
    assert "尚不能判断" in checked.summary and "未发现" not in checked.summary
    assert "no resolved" in alert_capability_error(req, "create_price_alert", {"symbol":"600519", "target_price":999999})
    invocation = SimpleNamespace(tool_name="check_watch_request", call_id="check", status="completed", summary=checked.summary, arguments={}, observed_at=datetime.now(UTC), source_data=[], result_data=checked.data)
    result = build_deterministic_assistant_result(task_id=1, answer="需澄清", invocations=[invocation], language="zh-CN")
    assert any("不能判断原条件是否支持" in item for item in result.missing_data)


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
