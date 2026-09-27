from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.automation.api.templates import (
    TemplateAgent,
    TemplatePayload,
    TemplateStock,
    TemplateStockAgent,
    export_template,
    import_template,
)
from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    AIModel,
    AIService,
    Account,
    AgentConfig,
    AppSettings,
    NotifyChannel,
    Position,
    Stock,
    StockAgent,
)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            AIService.__table__,
            AIModel.__table__,
            NotifyChannel.__table__,
            AgentConfig.__table__,
            AppSettings.__table__,
            Stock.__table__,
            StockAgent.__table__,
            Account.__table__,
            Position.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def test_import_filters_dangling_ids_and_creates_natural_key_records(monkeypatch):
    """悬空模型/渠道 ID 被过滤，Agent、股票和绑定仍按自然键创建。"""
    db = _session()
    monkeypatch.setitem(
        sys.modules, "server", SimpleNamespace(reload_scheduler=lambda: False)
    )
    service = AIService(name="OpenAI", base_url="https://example.com", api_key="")
    db.add(service)
    db.flush()
    model = AIModel(
        name="Existing model",
        service_id=service.id,
        model="existing-model",
        is_default=True,
    )
    channel = NotifyChannel(name="Existing channel", type="bark", config={})
    db.add_all([model, channel])
    db.commit()

    payload = TemplatePayload(
        agents=[
            TemplateAgent(
                name="daily_report",
                ai_model_id=model.id,
                notify_channel_ids=[channel.id, 98],
            ),
            TemplateAgent(
                name="tradingagents",
                ai_model_id=99,
                notify_channel_ids=[97],
            ),
        ],
        stocks=[
            TemplateStock(
                symbol="600519",
                name="贵州茅台",
                market="CN",
                agents=[
                    TemplateStockAgent(
                        agent_name="daily_report",
                        ai_model_id=96,
                        notify_channel_ids=[channel.id, 95],
                    )
                ],
            )
        ],
    )

    result = import_template(payload=payload, mode="merge", db=db)

    daily_report = db.query(AgentConfig).filter_by(name="daily_report").one()
    tradingagents = db.query(AgentConfig).filter_by(name="tradingagents").one()
    stock = db.query(Stock).filter_by(symbol="600519", market="CN").one()
    stock_agent = db.query(StockAgent).filter_by(stock_id=stock.id).one()

    assert daily_report.ai_model_id == model.id
    assert daily_report.notify_channel_ids == [channel.id]
    assert tradingagents.ai_model_id is None
    assert tradingagents.notify_channel_ids == []
    assert stock_agent.ai_model_id is None
    assert stock_agent.notify_channel_ids == [channel.id]
    expected_summary = {
        "updated_settings": 0,
        "created_agents": 2,
        "updated_agents": 0,
        "created_stocks": 1,
        "updated_stocks": 0,
        "created_stock_agents": 1,
        "updated_stock_agents": 0,
        "dropped_ai_model_refs": 2,
        "dropped_notify_channel_refs": 3,
    }
    assert {
        key: result["summary"][key] for key in expected_summary
    } == expected_summary
    assert {warning["code"] for warning in result["warnings"]} == {
        "missing_ai_model",
        "missing_notify_channel",
    }


def test_import_with_only_dangling_ids_finishes_without_foreign_key_error(monkeypatch):
    """目标库没有模型和渠道时，旧配置包仍可导入而不会返回 500。"""
    db = _session()
    monkeypatch.setitem(
        sys.modules, "server", SimpleNamespace(reload_scheduler=lambda: False)
    )
    payload = TemplatePayload(
        agents=[
            TemplateAgent(
                name="daily_report",
                ai_model_id=9,
                notify_channel_ids=[2, 3],
            ),
            TemplateAgent(name="tradingagents", ai_model_id=22),
        ]
    )

    result = import_template(payload=payload, mode="merge", db=db)

    rows = db.query(AgentConfig).order_by(AgentConfig.name).all()
    assert [(row.name, row.ai_model_id, row.notify_channel_ids) for row in rows] == [
        ("daily_report", None, []),
        ("tradingagents", None, []),
    ]
    assert result["ok"] is True
    assert result["summary"]["dropped_ai_model_refs"] == 2
    assert result["summary"]["dropped_notify_channel_refs"] == 2


def test_import_rejects_unsupported_template_version_with_stable_error_code():
    with pytest.raises(HTTPException) as exc_info:
        import_template(payload=TemplatePayload(version=3), mode="merge", db=None)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "code": "template_version_unsupported",
        "message": "不支持的配置包版本: 3",
    }


def test_v2_round_trip_recreates_sensitive_config_positions_and_relations(monkeypatch):
    """v2 配置包在 ID 不同的目标库中按自然键重建完整关系。"""
    monkeypatch.setitem(
        sys.modules, "server", SimpleNamespace(reload_scheduler=lambda: False)
    )
    source = _session()
    service = AIService(
        name="OpenAI", base_url="https://api.example.com/v1", api_key="secret-key"
    )
    source.add(service)
    source.flush()
    model = AIModel(
        name="Analysis Model",
        service_id=service.id,
        model="analysis-v1",
        is_default=True,
    )
    channel = NotifyChannel(
        name="My Bark",
        type="bark",
        config={"server": "https://bark.example.com", "device_key": "secret-device"},
        enabled=True,
        is_default=True,
    )
    source.add_all([model, channel])
    source.flush()
    agent = AgentConfig(
        name="daily_report",
        display_name="日报",
        description="",
        ai_model_id=model.id,
        notify_channel_ids=[channel.id],
    )
    stock = Stock(symbol="600519", name="贵州茅台", market="CN", sort_order=3)
    source.add_all([agent, stock])
    source.flush()
    source.add(
        StockAgent(
            stock_id=stock.id,
            agent_name="daily_report",
            ai_model_id=model.id,
            notify_channel_ids=[channel.id],
        )
    )
    account = Account(name="主账户", available_funds=12345.67, enabled=True)
    source.add(account)
    source.flush()
    source.add(
        Position(
            account_id=account.id,
            stock_id=stock.id,
            cost_price=1567.89,
            quantity=200,
            invested_amount=313578,
            sort_order=2,
            trading_style="long",
        )
    )
    source.commit()

    exported = export_template(
        include_internal=True,
        modules="settings,ai,notifications,agents,watchlist,portfolio",
        db=source,
    )
    assert exported["version"] == 2
    assert exported["ai_services"][0]["api_key"] == "secret-key"
    assert exported["notify_channels"][0]["config"]["device_key"] == "secret-device"
    assert exported["accounts"][0]["positions"][0]["quantity"] == 200

    target = _session()
    seed_service = AIService(name="Seed", base_url="https://seed.example.com", api_key="")
    target.add(seed_service)
    target.flush()
    seed_model = AIModel(name="Seed", service_id=seed_service.id, model="seed")
    seed_channel = NotifyChannel(name="Seed", type="telegram", config={})
    target.add_all([seed_model, seed_channel])
    target.commit()

    result = import_template(
        payload=TemplatePayload.model_validate(exported),
        mode="merge",
        modules=",".join(exported["modules"]),
        db=target,
    )

    imported_service = target.query(AIService).filter_by(name="OpenAI").one()
    imported_model = target.query(AIModel).filter_by(model="analysis-v1").one()
    imported_channel = target.query(NotifyChannel).filter_by(name="My Bark").one()
    imported_agent = target.query(AgentConfig).filter_by(name="daily_report").one()
    imported_stock = target.query(Stock).filter_by(symbol="600519", market="CN").one()
    imported_stock_agent = target.query(StockAgent).filter_by(stock_id=imported_stock.id).one()
    imported_account = target.query(Account).filter_by(name="主账户").one()
    imported_position = target.query(Position).filter_by(account_id=imported_account.id).one()

    assert imported_service.api_key == "secret-key"
    assert imported_model.id != model.id
    assert imported_model.is_default is True
    assert seed_model.is_default is False
    assert imported_channel.id != channel.id
    assert imported_channel.is_default is True
    assert imported_agent.ai_model_id == imported_model.id
    assert imported_agent.notify_channel_ids == [imported_channel.id]
    assert imported_stock_agent.ai_model_id == imported_model.id
    assert imported_stock_agent.notify_channel_ids == [imported_channel.id]
    assert imported_account.available_funds == 12345.67
    assert imported_position.stock_id == imported_stock.id
    assert imported_position.cost_price == 1567.89
    assert imported_position.quantity == 200
    assert imported_position.invested_amount == 313578
    assert imported_position.trading_style == "long"
    assert result["summary"]["created_ai_services"] == 1
    assert result["summary"]["created_ai_models"] == 1
    assert result["summary"]["created_notify_channels"] == 1
    assert result["summary"]["created_accounts"] == 1
    assert result["summary"]["created_positions"] == 1
    assert result["summary"]["dropped_ai_model_refs"] == 0
    assert result["summary"]["dropped_notify_channel_refs"] == 0


def test_export_only_contains_selected_modules():
    db = _session()
    db.add(Account(name="账户", available_funds=1))
    db.commit()

    exported = export_template(include_internal=True, modules="portfolio", db=db)

    assert exported["modules"] == ["portfolio"]
    assert "accounts" in exported
    assert "settings" not in exported
    assert "ai_services" not in exported
    assert "notify_channels" not in exported
    assert "agents" not in exported
    assert "stocks" not in exported


def test_template_does_not_export_or_restore_agent_output_language(monkeypatch):
    """旧配置包中的独立报告语言不应覆盖界面语言。"""
    monkeypatch.setitem(
        sys.modules, "server", SimpleNamespace(reload_scheduler=lambda: False)
    )
    source = _session()
    source.add(
        AgentConfig(
            name="tradingagents",
            display_name="TradingAgents",
            config={"output_language": "English", "timeout_minutes": 15},
        )
    )
    source.commit()

    exported = export_template(include_internal=True, modules="agents", db=source)
    assert exported["agents"][0]["config"] == {"timeout_minutes": 15}

    target = _session()
    target.add(
        AgentConfig(
            name="tradingagents",
            display_name="TradingAgents",
            config={"output_language": "Chinese", "monthly_budget_usd": 10},
        )
    )
    target.commit()
    legacy_payload = TemplatePayload.model_validate(
        {
            "version": 2,
            "modules": ["agents"],
            "agents": [
                {
                    "name": "tradingagents",
                    "config": {"output_language": "English", "timeout_minutes": 20},
                }
            ],
        }
    )

    import_template(payload=legacy_payload, mode="merge", modules="agents", db=target)

    restored = target.query(AgentConfig).filter_by(name="tradingagents").one()
    assert restored.config == {"monthly_budget_usd": 10, "timeout_minutes": 20}


def test_import_only_applies_selected_modules(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "server", SimpleNamespace(reload_scheduler=lambda: False)
    )
    db = _session()
    payload = TemplatePayload.model_validate(
        {
            "version": 2,
            "modules": ["ai", "portfolio"],
            "ai_services": [
                {
                    "name": "Should not import",
                    "base_url": "https://ai.example.com",
                    "api_key": "secret",
                    "models": [],
                }
            ],
            "accounts": [
                {
                    "name": "Only account",
                    "available_funds": 88,
                    "positions": [],
                }
            ],
        }
    )

    result = import_template(
        payload=payload,
        mode="merge",
        modules="portfolio",
        db=db,
    )

    assert db.query(AIService).count() == 0
    assert db.query(Account).filter_by(name="Only account").count() == 1
    assert result["modules"] == ["portfolio"]
