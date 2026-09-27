from __future__ import annotations

import sys
from types import SimpleNamespace

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.automation.api.templates import (
    TemplateAgent,
    TemplatePayload,
    TemplateStock,
    TemplateStockAgent,
    import_template,
)
from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    AIModel,
    AIService,
    AgentConfig,
    AppSettings,
    NotifyChannel,
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
    assert result["summary"] == {
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
