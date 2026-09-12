"""PanWatch business tool adapters exposed to the generic agent runtime."""

import asyncio
from types import SimpleNamespace

from pan_agent import ModelMessage, RunRequest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.modules.assistant.tools as assistant_tools
from src.platform.persistence.database import Base
from src.platform.persistence.models import Account, Position, PriceAlertHit, PriceAlertRule, Stock


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _request() -> RunRequest:
    return RunRequest(
        run_id="tools-test", messages=[ModelMessage(role="user", content="测试工具")]
    )


def test_portfolio_tool_is_read_only_and_includes_provenance():
    engine, session = _session()
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    account = Account(name="默认账户")
    session.add_all([stock, account])
    session.commit()
    session.add(
        Position(account_id=account.id, stock_id=stock.id, cost_price=1500, quantity=10)
    )
    session.commit()

    registry = assistant_tools.build_panwatch_tool_registry(session)
    result = asyncio.run(registry.execute("get_portfolio", _request(), {}))

    assert result.ok is True
    assert "贵州茅台" in result.summary
    assert result.sources[0].name == "PanWatch 持仓"
    session.close()
    engine.dispose()


def test_quote_tool_returns_compact_fact_summary(monkeypatch):
    engine, session = _session()
    monkeypatch.setattr(
        assistant_tools,
        "md_quote_rows",
        lambda *_: [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "market": "CN",
                "current_price": 1800.0,
                "change_pct": 1.2,
                "high_price": 1812.0,
                "low_price": 1775.0,
                "open_price": 1780.0,
                "prev_close": 1778.0,
                "volume": 123.0,
                "turnover": 456.0,
            }
        ],
        raising=False,
    )

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "get_stock_quote",
            _request(),
            {"symbol": "600519", "market": "CN"},
        )
    )

    assert result.ok is True
    assert result.data["symbol"] == "600519"
    assert "1800" in result.summary
    session.close()
    engine.dispose()


def test_quote_tool_returns_controlled_failure_without_quote(monkeypatch):
    engine, session = _session()
    monkeypatch.setattr(assistant_tools, "md_quote_rows", lambda *_: [], raising=False)

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "get_stock_quote",
            _request(),
            {"symbol": "600519", "market": "CN"},
        )
    )

    assert result.ok is False
    assert result.error_code == "quote_unavailable"
    session.close()
    engine.dispose()


def test_kline_summary_tool_returns_compact_summary(monkeypatch):
    class _Collector:
        def __init__(self, _market):
            pass

        def get_kline_summary(self, symbol):
            return {"symbol": symbol, "trend": "up", "ma5": 10.0, "ma20": 9.0}

    engine, session = _session()
    monkeypatch.setattr(assistant_tools, "KlineCollector", _Collector, raising=False)

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "get_kline_summary",
            _request(),
            {"symbol": "600519", "market": "CN"},
        )
    )

    assert result.ok is True
    assert result.data["trend"] == "up"
    session.close()
    engine.dispose()


def test_news_tool_limits_compact_items(monkeypatch):
    engine, session = _session()
    monkeypatch.setattr(
        assistant_tools,
        "md_news",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                title="贵州茅台发布公告",
                source="eastmoney",
                publish_time="2026-09-12T08:00:00Z",
                url="https://example.test/1",
                importance=2,
            ),
            SimpleNamespace(
                title="行业动态",
                source="xueqiu",
                publish_time="2026-09-12T07:00:00Z",
                url="https://example.test/2",
                importance=1,
            ),
        ],
        raising=False,
    )

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "get_stock_news",
            _request(),
            {"symbol": "600519", "market": "CN", "limit": 1},
        )
    )

    assert result.ok is True
    assert result.data["items"] == [
        {
            "title": "贵州茅台发布公告",
            "source": "eastmoney",
            "published_at": "2026-09-12T08:00:00Z",
            "url": "https://example.test/1",
            "importance": 2,
        }
    ]
    session.close()
    engine.dispose()


def test_create_price_alert_validates_and_persists_rule():
    engine, session = _session()
    session.add(Stock(symbol="600519", name="贵州茅台", market="CN"))
    session.commit()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "create_price_alert",
            _request(),
            {
                "symbol": "600519",
                "market": "CN",
                "direction": "above",
                "target_price": 1800,
            },
        )
    )

    rule = session.query(PriceAlertRule).one()
    assert result.ok is True
    assert rule.condition_group == {
        "op": "and",
        "items": [{"type": "price", "op": ">=", "value": 1800.0}],
    }
    assert "价格 ≥ 1800" in result.summary
    session.close()
    engine.dispose()


def test_create_price_alert_registers_a_known_quote_before_writing_rule(monkeypatch):
    engine, session = _session()
    monkeypatch.setattr(
        assistant_tools,
        "md_quote_rows",
        lambda *_: [{"symbol": "02269", "name": "药明生物", "market": "HK"}],
        raising=False,
    )

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "create_price_alert",
            _request(),
            {
                "symbol": "02269",
                "market": "HK",
                "direction": "below",
                "target_price": 40,
            },
        )
    )

    stock = session.query(Stock).one()
    rule = session.query(PriceAlertRule).one()
    assert result.ok is True
    assert result.data["stock_registered"] is True
    assert stock.symbol == "02269"
    assert stock.market == "HK"
    assert stock.name == "药明生物"
    assert rule.stock_id == stock.id
    session.close()
    engine.dispose()


def test_create_price_alert_does_not_write_for_unknown_stock(monkeypatch):
    engine, session = _session()
    monkeypatch.setattr(assistant_tools, "md_quote_rows", lambda *_: [], raising=False)

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "create_price_alert",
            _request(),
            {
                "symbol": "000000",
                "market": "CN",
                "direction": "below",
                "target_price": 1,
            },
        )
    )

    assert result.ok is False
    assert result.error_code == "stock_not_found"
    assert session.query(PriceAlertRule).count() == 0
    session.close()
    engine.dispose()


def test_get_price_alerts_returns_compact_rules_and_supports_symbol_filter():
    engine, session = _session()
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    other = Stock(symbol="601238", name="广汽集团", market="CN")
    session.add_all([stock, other])
    session.flush()
    session.add_all(
        [
            PriceAlertRule(
                stock_id=stock.id,
                name="茅台突破",
                enabled=True,
                condition_group={"op": "and", "items": [{"type": "price", "op": ">=", "value": 1800}]},
                cooldown_minutes=30,
            ),
            PriceAlertRule(
                stock_id=other.id,
                name="广汽回落",
                enabled=False,
                condition_group={"op": "and", "items": [{"type": "price", "op": "<=", "value": 10}]},
            ),
        ]
    )
    session.commit()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "get_price_alerts",
            _request(),
            {"symbol": "600519", "market": "CN"},
        )
    )

    assert result.ok is True
    assert result.data["count"] == 1
    assert result.data["items"] == [
        {
            "rule_id": 1,
            "name": "茅台突破",
            "symbol": "600519",
            "stock_name": "贵州茅台",
            "market": "CN",
            "enabled": True,
            "direction": "above",
            "target_price": 1800.0,
            "cooldown_minutes": 30,
            "max_triggers_per_day": 3,
            "repeat_mode": "repeat",
        }
    ]
    session.close()
    engine.dispose()


def test_update_price_alert_changes_rule_and_resets_trigger_state():
    engine, session = _session()
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    session.add(stock)
    session.flush()
    rule = PriceAlertRule(
        stock_id=stock.id,
        name="旧提醒",
        enabled=True,
        condition_group={"op": "and", "items": [{"type": "price", "op": ">=", "value": 1800}]},
        trigger_count_today=2,
        trigger_date="2026-09-12",
    )
    session.add(rule)
    session.commit()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "update_price_alert",
            _request(),
            {
                "rule_id": rule.id,
                "name": "茅台回落提醒",
                "enabled": False,
                "direction": "below",
                "target_price": 1700,
                "cooldown_minutes": 45,
            },
        )
    )

    session.refresh(rule)
    assert result.ok is True
    assert rule.name == "茅台回落提醒"
    assert rule.enabled is False
    assert rule.condition_group == {
        "op": "and",
        "items": [{"type": "price", "op": "<=", "value": 1700.0}],
    }
    assert rule.cooldown_minutes == 45
    assert rule.trigger_count_today == 0
    assert rule.trigger_date == ""
    session.close()
    engine.dispose()


def test_update_price_alert_returns_controlled_failure_for_unknown_rule():
    engine, session = _session()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "update_price_alert", _request(), {"rule_id": 999, "enabled": False}
        )
    )

    assert result.ok is False
    assert result.error_code == "price_alert_not_found"
    session.close()
    engine.dispose()


def test_delete_price_alert_removes_rule_and_its_hits():
    engine, session = _session()
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    session.add(stock)
    session.flush()
    rule = PriceAlertRule(stock_id=stock.id, name="删除我")
    session.add(rule)
    session.flush()
    session.add(
        PriceAlertHit(
            rule_id=rule.id,
            stock_id=stock.id,
            trigger_bucket="202609121300",
            trigger_snapshot={},
        )
    )
    session.commit()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "delete_price_alert", _request(), {"rule_id": rule.id}
        )
    )

    assert result.ok is True
    assert session.query(PriceAlertRule).count() == 0
    assert session.query(PriceAlertHit).count() == 0
    session.close()
    engine.dispose()
