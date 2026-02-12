import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.web.database import get_db
from src.web.models import AgentConfig, AppSettings, Stock, StockAgent
from src.core.agent_catalog import AGENT_KIND_CAPABILITY, infer_agent_kind


logger = logging.getLogger(__name__)
router = APIRouter()


class TemplateAgent(BaseModel):
    name: str
    kind: str | None = None
    visible: bool | None = None
    lifecycle_status: str | None = None
    replaced_by: str | None = None
    display_order: int | None = None
    enabled: bool = True
    schedule: str = ""
    execution_mode: str = "batch"
    ai_model_id: int | None = None
    notify_channel_ids: list[int] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class TemplateStockAgent(BaseModel):
    agent_name: str
    schedule: str = ""
    ai_model_id: int | None = None
    notify_channel_ids: list[int] = Field(default_factory=list)


class TemplateStock(BaseModel):
    symbol: str
    name: str
    market: str
    agents: list[TemplateStockAgent] = Field(default_factory=list)


class TemplatePayload(BaseModel):
    version: int = 1
    exported_at: str = ""
    settings: dict[str, str] = Field(default_factory=dict)
    agents: list[TemplateAgent] = Field(default_factory=list)
    stocks: list[TemplateStock] = Field(default_factory=list)


_SETTINGS_KEYS = {
    "http_proxy",
    "notify_quiet_hours",
    "notify_retry_attempts",
    "notify_retry_backoff_seconds",
    "notify_dedupe_ttl_overrides",
}


@router.get("/export")
def export_template(
    include_internal: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """导出当前配置为可导入的配置包 JSON"""
    settings_rows = (
        db.query(AppSettings).filter(AppSettings.key.in_(sorted(_SETTINGS_KEYS))).all()
    )
    settings = {r.key: (r.value or "") for r in settings_rows}

    query = db.query(AgentConfig)
    if not include_internal:
        query = query.filter(AgentConfig.visible == True)
    agents_rows = query.order_by(AgentConfig.display_order.asc(), AgentConfig.name.asc()).all()
    agents = []
    for a in agents_rows:
        kind = (a.kind or "").strip() or infer_agent_kind(a.name)
        agents.append(
            {
                "name": a.name,
                "kind": kind,
                "visible": bool(a.visible),
                "lifecycle_status": a.lifecycle_status or "active",
                "replaced_by": a.replaced_by or "",
                "display_order": int(a.display_order or 0),
                "enabled": bool(a.enabled),
                "schedule": a.schedule or "",
                "execution_mode": a.execution_mode or "batch",
                "ai_model_id": a.ai_model_id,
                "notify_channel_ids": a.notify_channel_ids or [],
                "config": a.config or {},
            }
        )

    stocks_rows = db.query(Stock).order_by(Stock.market.asc(), Stock.symbol.asc()).all()
    stocks = []
    for s in stocks_rows:
        sa_rows = (
            db.query(StockAgent)
            .filter(StockAgent.stock_id == s.id)
            .order_by(StockAgent.agent_name.asc())
            .all()
        )
        stocks.append(
            {
                "symbol": s.symbol,
                "name": s.name,
                "market": s.market,
                "agents": [
                    {
                        "agent_name": sa.agent_name,
                        "schedule": sa.schedule or "",
                        "ai_model_id": sa.ai_model_id,
                        "notify_channel_ids": sa.notify_channel_ids or [],
                    }
                    for sa in sa_rows
                ],
            }
        )

    return {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
        "agents": agents,
        "stocks": stocks,
    }


@router.post("/import")
def import_template(
    payload: TemplatePayload,
    mode: str = Query(
        "merge", description="merge=合并更新, replace=替换(仅对 payload 涵盖的数据)"
    ),
    db: Session = Depends(get_db),
):
    """导入配置包。默认 merge：仅更新/创建 payload 中包含的对象。"""

    if payload.version != 1:
        raise HTTPException(400, f"不支持的配置包版本: {payload.version}")
    if mode not in ("merge", "replace"):
        raise HTTPException(400, "mode 仅支持 merge/replace")

    updated_settings = 0
    created_stocks = 0
    updated_stocks = 0
    created_agents = 0
    updated_agents = 0
    created_stock_agents = 0
    updated_stock_agents = 0

    # Settings
    for k, v in (payload.settings or {}).items():
        if k not in _SETTINGS_KEYS:
            continue
        row = db.query(AppSettings).filter(AppSettings.key == k).first()
        if row:
            row.value = str(v or "")
        else:
            db.add(AppSettings(key=k, value=str(v or ""), description=""))
        updated_settings += 1

    # Agents
    for a in payload.agents or []:
        row = db.query(AgentConfig).filter(AgentConfig.name == a.name).first()
        if not row:
            # Minimal create; display_name/description fall back to name.
            row = AgentConfig(name=a.name, display_name=a.name, description="")
            db.add(row)
            created_agents += 1
        else:
            updated_agents += 1

        row.kind = (a.kind or "").strip() or infer_agent_kind(a.name)
        row.visible = (
            bool(a.visible)
            if a.visible is not None
            else (row.kind != AGENT_KIND_CAPABILITY)
        )
        row.lifecycle_status = a.lifecycle_status or (
            "deprecated" if row.kind == AGENT_KIND_CAPABILITY else "active"
        )
        row.replaced_by = a.replaced_by or row.replaced_by or ""
        row.display_order = int(a.display_order or row.display_order or 0)
        row.enabled = bool(a.enabled)
        row.schedule = a.schedule or ""
        row.execution_mode = a.execution_mode or "batch"
        row.ai_model_id = a.ai_model_id
        row.notify_channel_ids = a.notify_channel_ids or []
        if row.kind == AGENT_KIND_CAPABILITY:
            row.enabled = False
            row.schedule = ""
        cfg = row.config or {}
        if mode == "replace":
            row.config = a.config or {}
        else:
            # merge
            if isinstance(cfg, dict) and isinstance(a.config, dict):
                cfg.update(a.config)
                row.config = cfg
            else:
                row.config = a.config or {}

    # Stocks + StockAgents
    for s in payload.stocks or []:
        stock = (
            db.query(Stock)
            .filter(Stock.symbol == s.symbol, Stock.market == s.market)
            .first()
        )
        if not stock:
            stock = Stock(symbol=s.symbol, name=s.name, market=s.market)
            db.add(stock)
            db.flush()  # assign id
            created_stocks += 1
        else:
            updated_stocks += 1
            stock.name = s.name or stock.name

        if not s.agents:
            continue

        existing = db.query(StockAgent).filter(StockAgent.stock_id == stock.id).all()
        existing_map = {x.agent_name: x for x in existing}
        desired_names = {x.agent_name for x in s.agents}

        # replace mode: remove stock-agent not in payload for this stock
        if mode == "replace":
            for x in existing:
                if x.agent_name not in desired_names:
                    db.delete(x)

        for sa in s.agents:
            row = existing_map.get(sa.agent_name)
            if not row:
                row = StockAgent(
                    stock_id=stock.id,
                    agent_name=sa.agent_name,
                    schedule=sa.schedule or "",
                    ai_model_id=sa.ai_model_id,
                    notify_channel_ids=sa.notify_channel_ids or [],
                )
                db.add(row)
                created_stock_agents += 1
            else:
                row.schedule = sa.schedule or ""
                row.ai_model_id = sa.ai_model_id
                row.notify_channel_ids = sa.notify_channel_ids or []
                updated_stock_agents += 1

    db.commit()
    logger.info(
        f"导入配置包: settings={updated_settings} agents(+{created_agents}/~{updated_agents}) "
        f"stocks(+{created_stocks}/~{updated_stocks}) stock_agents(+{created_stock_agents}/~{updated_stock_agents})"
    )

    # Best-effort: reload scheduler so schedule changes take effect immediately.
    reloaded = False
    try:
        from server import reload_scheduler

        reloaded = bool(reload_scheduler())
    except Exception:
        reloaded = False

    return {
        "ok": True,
        "mode": mode,
        "scheduler_reloaded": reloaded,
        "summary": {
            "updated_settings": updated_settings,
            "created_agents": created_agents,
            "updated_agents": updated_agents,
            "created_stocks": created_stocks,
            "updated_stocks": updated_stocks,
            "created_stock_agents": created_stock_agents,
            "updated_stock_agents": updated_stock_agents,
        },
    }
