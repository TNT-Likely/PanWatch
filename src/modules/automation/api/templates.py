import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.platform.persistence.database import get_db
from src.web.errors import api_error
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
from src.modules.automation.agent_catalog import AGENT_KIND_CAPABILITY, RETIRED_AGENT_NAMES, infer_agent_kind


logger = logging.getLogger(__name__)
router = APIRouter()


class TemplateAIModelRef(BaseModel):
    service_name: str
    service_base_url: str = ""
    model: str


class TemplateNotifyChannelRef(BaseModel):
    name: str
    type: str


class TemplateAIModel(BaseModel):
    source_id: int | None = None
    name: str
    model: str
    is_default: bool = False


class TemplateAIService(BaseModel):
    source_id: int | None = None
    name: str
    base_url: str
    api_key: str = ""
    models: list[TemplateAIModel] = Field(default_factory=list)


class TemplateNotifyChannel(BaseModel):
    source_id: int | None = None
    name: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False


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
    ai_model_ref: TemplateAIModelRef | None = None
    notify_channel_ids: list[int] = Field(default_factory=list)
    notify_channel_refs: list[TemplateNotifyChannelRef] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class TemplateStockAgent(BaseModel):
    agent_name: str
    schedule: str = ""
    ai_model_id: int | None = None
    ai_model_ref: TemplateAIModelRef | None = None
    notify_channel_ids: list[int] = Field(default_factory=list)
    notify_channel_refs: list[TemplateNotifyChannelRef] = Field(default_factory=list)


class TemplateStock(BaseModel):
    symbol: str
    name: str
    market: str
    sort_order: int = 0
    agents: list[TemplateStockAgent] = Field(default_factory=list)


class TemplatePosition(BaseModel):
    symbol: str
    name: str
    market: str
    cost_price: float
    quantity: int
    invested_amount: float | None = None
    sort_order: int = 0
    trading_style: str = "swing"


class TemplateAccount(BaseModel):
    name: str
    available_funds: float = 0
    enabled: bool = True
    positions: list[TemplatePosition] = Field(default_factory=list)


class TemplatePayload(BaseModel):
    version: int = 1
    exported_at: str = ""
    modules: list[str] = Field(default_factory=list)
    settings: dict[str, str] = Field(default_factory=dict)
    ai_services: list[TemplateAIService] = Field(default_factory=list)
    notify_channels: list[TemplateNotifyChannel] = Field(default_factory=list)
    agents: list[TemplateAgent] = Field(default_factory=list)
    stocks: list[TemplateStock] = Field(default_factory=list)
    accounts: list[TemplateAccount] = Field(default_factory=list)


_SETTINGS_KEYS = {
    "http_proxy",
    "notify_quiet_hours",
    "notify_retry_attempts",
    "notify_retry_backoff_seconds",
    "notify_dedupe_ttl_overrides",
}

_MODULE_ORDER = (
    "settings",
    "ai",
    "notifications",
    "agents",
    "watchlist",
    "portfolio",
)
_MODULES = set(_MODULE_ORDER)
_LEGACY_MODULES = {"settings", "agents", "watchlist"}


def _portable_agent_config(config: dict | None) -> dict:
    value = dict(config or {})
    value.pop("output_language", None)
    return value


def _selected_modules(
    raw: str | None, payload: TemplatePayload | None = None
) -> set[str]:
    if raw:
        selected = {item.strip() for item in raw.split(",") if item.strip()}
    elif payload and payload.modules:
        selected = set(payload.modules)
    elif payload and payload.version == 1:
        selected = set(_LEGACY_MODULES)
    else:
        selected = set(_MODULES)

    invalid = selected - _MODULES
    if invalid:
        raise api_error(400, "template_module_invalid", f"不支持的配置模块: {', '.join(sorted(invalid))}")
    if not selected:
        raise api_error(400, "template_module_required", "请至少选择一个配置模块")
    return selected


def _model_ref(model: AIModel | None) -> dict[str, Any] | None:
    if not model or not model.service:
        return None
    return {
        "service_name": model.service.name,
        "service_base_url": model.service.base_url,
        "model": model.model,
    }


def _channel_ref(channel: NotifyChannel | None) -> dict[str, str] | None:
    if not channel:
        return None
    return {"name": channel.name, "type": channel.type}


@router.get("/export")
def export_template(
    include_internal: bool = Query(default=True),
    modules: str | None = None,
    db: Session = Depends(get_db),
):
    """导出当前配置为可导入的配置包 JSON"""
    selected = _selected_modules(modules)
    result: dict[str, Any] = {
        "version": 2,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "modules": [module for module in _MODULE_ORDER if module in selected],
    }

    # Version 2 uses stable natural-key references while retaining source IDs so
    # old readers can still inspect the package.

    if "settings" in selected:
        settings_rows = (
            db.query(AppSettings)
            .filter(AppSettings.key.in_(sorted(_SETTINGS_KEYS)))
            .all()
        )
        result["settings"] = {r.key: (r.value or "") for r in settings_rows}

    if "ai" in selected:
        services = db.query(AIService).order_by(AIService.id.asc()).all()
        result["ai_services"] = [
            {
                "source_id": service.id,
                "name": service.name,
                "base_url": service.base_url,
                "api_key": service.api_key or "",
                "models": [
                    {
                        "source_id": model.id,
                        "name": model.name,
                        "model": model.model,
                        "is_default": bool(model.is_default),
                    }
                    for model in sorted(service.models, key=lambda item: item.id)
                ],
            }
            for service in services
        ]

    if "notifications" in selected:
        channel_rows = db.query(NotifyChannel).order_by(NotifyChannel.id.asc()).all()
        result["notify_channels"] = [
            {
                "source_id": channel.id,
                "name": channel.name,
                "type": channel.type,
                "config": channel.config or {},
                "enabled": bool(channel.enabled),
                "is_default": bool(channel.is_default),
            }
            for channel in channel_rows
        ]

    # References are emitted by stable keys even when the referenced module is
    # not selected, allowing them to reconnect to an existing target config.
    model_by_id = {model.id: model for model in db.query(AIModel).all()}
    channel_by_id = {channel.id: channel for channel in db.query(NotifyChannel).all()}

    if "agents" in selected:
        query = db.query(AgentConfig)
        if not include_internal:
            query = query.filter(AgentConfig.visible == True)
        agents_rows = query.order_by(
            AgentConfig.display_order.asc(), AgentConfig.name.asc()
        ).all()
        result["agents"] = []
        for agent in agents_rows:
            kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
            result["agents"].append(
                {
                    "name": agent.name,
                    "kind": kind,
                    "visible": bool(agent.visible),
                    "lifecycle_status": agent.lifecycle_status or "active",
                    "replaced_by": agent.replaced_by or "",
                    "display_order": int(agent.display_order or 0),
                    "enabled": bool(agent.enabled),
                    "schedule": agent.schedule or "",
                    "execution_mode": agent.execution_mode or "batch",
                    "ai_model_id": agent.ai_model_id,
                    "ai_model_ref": _model_ref(model_by_id.get(agent.ai_model_id)),
                    "notify_channel_ids": agent.notify_channel_ids or [],
                    "notify_channel_refs": [
                        ref
                        for channel_id in agent.notify_channel_ids or []
                        if (ref := _channel_ref(channel_by_id.get(channel_id)))
                    ],
                    "config": _portable_agent_config(agent.config),
                }
            )

    if "watchlist" in selected:
        stocks_rows = (
            db.query(Stock)
            .order_by(Stock.sort_order.asc(), Stock.market.asc(), Stock.symbol.asc())
            .all()
        )
        result["stocks"] = []
        for stock in stocks_rows:
            stock_agents = (
                db.query(StockAgent)
                .filter(StockAgent.stock_id == stock.id)
                .order_by(StockAgent.agent_name.asc())
                .all()
            )
            result["stocks"].append(
                {
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "market": stock.market,
                    "sort_order": int(stock.sort_order or 0),
                    "agents": [
                        {
                            "agent_name": stock_agent.agent_name,
                            "schedule": stock_agent.schedule or "",
                            "ai_model_id": stock_agent.ai_model_id,
                            "ai_model_ref": _model_ref(
                                model_by_id.get(stock_agent.ai_model_id)
                            ),
                            "notify_channel_ids": stock_agent.notify_channel_ids
                            or [],
                            "notify_channel_refs": [
                                ref
                                for channel_id in stock_agent.notify_channel_ids or []
                                if (
                                    ref := _channel_ref(
                                        channel_by_id.get(channel_id)
                                    )
                                )
                            ],
                        }
                        for stock_agent in stock_agents
                    ],
                }
            )

    if "portfolio" in selected:
        accounts = db.query(Account).order_by(Account.id.asc()).all()
        result["accounts"] = [
            {
                "name": account.name,
                "available_funds": float(account.available_funds or 0),
                "enabled": bool(account.enabled),
                "positions": [
                    {
                        "symbol": position.stock.symbol,
                        "name": position.stock.name,
                        "market": position.stock.market,
                        "cost_price": float(position.cost_price),
                        "quantity": int(position.quantity),
                        "invested_amount": position.invested_amount,
                        "sort_order": int(position.sort_order or 0),
                        "trading_style": position.trading_style or "swing",
                    }
                    for position in sorted(
                        account.positions,
                        key=lambda item: (item.sort_order or 0, item.id),
                    )
                    if position.stock
                ],
            }
            for account in accounts
        ]

    return result


@router.post("/import")
def import_template(
    payload: TemplatePayload,
    mode: str = Query(
        "merge", description="merge=合并更新, replace=替换(仅对 payload 涵盖的数据)"
    ),
    modules: str | None = None,
    db: Session = Depends(get_db),
):
    """导入配置包。默认 merge：仅更新/创建 payload 中包含的对象。"""

    if payload.version not in (1, 2):
        raise api_error(400, "template_version_unsupported", f"不支持的配置包版本: {payload.version}")
    if mode not in ("merge", "replace"):
        raise api_error(400, "template_mode_invalid", "mode 仅支持 merge/replace")
    selected = _selected_modules(modules, payload)

    updated_settings = 0
    created_ai_services = 0
    updated_ai_services = 0
    created_ai_models = 0
    updated_ai_models = 0
    created_notify_channels = 0
    updated_notify_channels = 0
    created_stocks = 0
    updated_stocks = 0
    created_agents = 0
    updated_agents = 0
    created_stock_agents = 0
    updated_stock_agents = 0
    created_accounts = 0
    updated_accounts = 0
    created_positions = 0
    updated_positions = 0
    dropped_ai_model_refs = 0
    dropped_notify_channel_refs = 0
    warnings: list[dict[str, Any]] = []

    # Import dependency modules first. Their source IDs are deliberately not
    # reused; later relationships resolve against stable natural keys.
    if "ai" in selected:
        for service_data in payload.ai_services or []:
            service = (
                db.query(AIService)
                .filter(
                    AIService.name == service_data.name,
                    AIService.base_url == service_data.base_url,
                )
                .first()
            )
            if not service:
                service = AIService(
                    name=service_data.name,
                    base_url=service_data.base_url,
                    api_key=service_data.api_key or "",
                )
                db.add(service)
                db.flush()
                created_ai_services += 1
            else:
                service.api_key = service_data.api_key or ""
                updated_ai_services += 1

            for model_data in service_data.models or []:
                model = (
                    db.query(AIModel)
                    .filter(
                        AIModel.service_id == service.id,
                        AIModel.model == model_data.model,
                    )
                    .first()
                )
                if not model:
                    model = AIModel(
                        service_id=service.id,
                        name=model_data.name or model_data.model,
                        model=model_data.model,
                    )
                    db.add(model)
                    created_ai_models += 1
                else:
                    updated_ai_models += 1
                model.name = model_data.name or model_data.model
                model.is_default = bool(model_data.is_default)
                if model.is_default:
                    db.flush()
                    db.query(AIModel).filter(AIModel.id != model.id).update(
                        {"is_default": False}, synchronize_session=False
                    )
            db.flush()

    if "notifications" in selected:
        for channel_data in payload.notify_channels or []:
            channel = (
                db.query(NotifyChannel)
                .filter(
                    NotifyChannel.name == channel_data.name,
                    NotifyChannel.type == channel_data.type,
                )
                .first()
            )
            if not channel:
                channel = NotifyChannel(
                    name=channel_data.name,
                    type=channel_data.type,
                )
                db.add(channel)
                db.flush()
                created_notify_channels += 1
            else:
                updated_notify_channels += 1
            channel.config = channel_data.config or {}
            channel.enabled = bool(channel_data.enabled)
            channel.is_default = bool(channel_data.is_default)
            if channel.is_default:
                db.query(NotifyChannel).filter(NotifyChannel.id != channel.id).update(
                    {"is_default": False}, synchronize_session=False
                )
        db.flush()

    # 配置包中的自增 ID 只在导出实例内有意义。目标库存在对应记录时保留，
    # v2 优先使用自然键引用；v1 仅在目标库恰好有同 ID 时兼容保留。
    requested_model_ids = {
        a.ai_model_id for a in payload.agents or [] if a.ai_model_id is not None
    }
    requested_channel_ids = {
        channel_id
        for a in payload.agents or []
        for channel_id in a.notify_channel_ids or []
    }
    for stock in payload.stocks or []:
        for stock_agent in stock.agents or []:
            if stock_agent.ai_model_id is not None:
                requested_model_ids.add(stock_agent.ai_model_id)
            requested_channel_ids.update(stock_agent.notify_channel_ids or [])
    valid_model_ids = (
        {
            row[0]
            for row in db.query(AIModel.id)
            .filter(AIModel.id.in_(requested_model_ids))
            .all()
        }
        if requested_model_ids
        else set()
    )
    valid_channel_ids = (
        {
            row[0]
            for row in db.query(NotifyChannel.id)
            .filter(NotifyChannel.id.in_(requested_channel_ids))
            .all()
        }
        if requested_channel_ids
        else set()
    )

    def resolve_model_id(
        model_id: int | None,
        model_ref: TemplateAIModelRef | None,
        *,
        resource: str,
        resource_key: str,
    ) -> int | None:
        nonlocal dropped_ai_model_refs
        if model_ref:
            query = (
                db.query(AIModel)
                .join(AIService, AIService.id == AIModel.service_id)
                .filter(
                    AIService.name == model_ref.service_name,
                    AIModel.model == model_ref.model,
                )
            )
            if model_ref.service_base_url:
                query = query.filter(
                    AIService.base_url == model_ref.service_base_url
                )
            model = query.first()
            if model:
                return model.id
            dropped_ai_model_refs += 1
            warnings.append(
                {
                    "code": "missing_ai_model",
                    "resource": resource,
                    "resource_key": resource_key,
                    "reference": model_ref.model_dump(),
                }
            )
            return None
        if model_id is None or model_id in valid_model_ids:
            return model_id
        dropped_ai_model_refs += 1
        warnings.append(
            {
                "code": "missing_ai_model",
                "resource": resource,
                "resource_key": resource_key,
                "reference_ids": [model_id],
            }
        )
        return None

    def resolve_channel_ids(
        channel_ids: list[int],
        channel_refs: list[TemplateNotifyChannelRef],
        *,
        resource: str,
        resource_key: str,
    ) -> list[int]:
        nonlocal dropped_notify_channel_refs
        if channel_refs:
            resolved_ids: list[int] = []
            missing_refs: list[dict[str, str]] = []
            for channel_ref in channel_refs:
                channel = (
                    db.query(NotifyChannel)
                    .filter(
                        NotifyChannel.name == channel_ref.name,
                        NotifyChannel.type == channel_ref.type,
                    )
                    .first()
                )
                if channel:
                    if channel.id not in resolved_ids:
                        resolved_ids.append(channel.id)
                else:
                    dropped_notify_channel_refs += 1
                    missing_refs.append(channel_ref.model_dump())
            if missing_refs:
                warnings.append(
                    {
                        "code": "missing_notify_channel",
                        "resource": resource,
                        "resource_key": resource_key,
                        "references": missing_refs,
                    }
                )
            return resolved_ids
        missing_ids = sorted(
            {
                channel_id
                for channel_id in channel_ids
                if channel_id not in valid_channel_ids
            }
        )
        if missing_ids:
            dropped_notify_channel_refs += sum(
                1 for channel_id in channel_ids if channel_id not in valid_channel_ids
            )
            warnings.append(
                {
                    "code": "missing_notify_channel",
                    "resource": resource,
                    "resource_key": resource_key,
                    "reference_ids": missing_ids,
                }
            )
        return [
            channel_id for channel_id in channel_ids if channel_id in valid_channel_ids
        ]

    # Settings
    if "settings" in selected:
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
    for a in payload.agents if "agents" in selected else []:
        if a.name in RETIRED_AGENT_NAMES:
            warnings.append({"code": "retired_agent", "resource": "agent", "resource_key": a.name})
            continue
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
        row.ai_model_id = resolve_model_id(
            a.ai_model_id,
            a.ai_model_ref,
            resource="agent",
            resource_key=a.name,
        )
        row.notify_channel_ids = resolve_channel_ids(
            a.notify_channel_ids or [],
            a.notify_channel_refs or [],
            resource="agent",
            resource_key=a.name,
        )
        if row.kind == AGENT_KIND_CAPABILITY:
            row.enabled = False
            row.schedule = ""
        cfg = row.config or {}
        imported_config = _portable_agent_config(a.config)
        if mode == "replace":
            row.config = imported_config
        else:
            # merge
            if isinstance(cfg, dict):
                merged_config = _portable_agent_config(cfg)
                merged_config.update(imported_config)
                row.config = merged_config
            else:
                row.config = imported_config

    # Stocks + StockAgents
    for s in payload.stocks if "watchlist" in selected else []:
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
        stock.sort_order = int(s.sort_order or 0)

        existing = db.query(StockAgent).filter(StockAgent.stock_id == stock.id).all()
        existing_map = {x.agent_name: x for x in existing}
        desired_names = {x.agent_name for x in s.agents if x.agent_name not in RETIRED_AGENT_NAMES}

        # replace mode: remove stock-agent not in payload for this stock
        if mode == "replace":
            for x in existing:
                if x.agent_name not in desired_names:
                    db.delete(x)

        if not s.agents:
            continue

        for sa in s.agents:
            resource_key = f"{s.market}:{s.symbol}:{sa.agent_name}"
            if sa.agent_name in RETIRED_AGENT_NAMES:
                warnings.append({"code": "retired_agent", "resource": "stock_agent", "resource_key": resource_key})
                continue
            ai_model_id = resolve_model_id(
                sa.ai_model_id,
                sa.ai_model_ref,
                resource="stock_agent",
                resource_key=resource_key,
            )
            notify_channel_ids = resolve_channel_ids(
                sa.notify_channel_ids or [],
                sa.notify_channel_refs or [],
                resource="stock_agent",
                resource_key=resource_key,
            )
            row = existing_map.get(sa.agent_name)
            if not row:
                row = StockAgent(
                    stock_id=stock.id,
                    agent_name=sa.agent_name,
                    schedule=sa.schedule or "",
                    ai_model_id=ai_model_id,
                    notify_channel_ids=notify_channel_ids,
                )
                db.add(row)
                created_stock_agents += 1
            else:
                row.schedule = sa.schedule or ""
                row.ai_model_id = ai_model_id
                row.notify_channel_ids = notify_channel_ids
                updated_stock_agents += 1

    # Accounts + positions. A position can be imported independently of the
    # watchlist module; its stock dependency is created by market + symbol.
    for account_data in payload.accounts if "portfolio" in selected else []:
        account = db.query(Account).filter(Account.name == account_data.name).first()
        if not account:
            account = Account(name=account_data.name)
            db.add(account)
            db.flush()
            created_accounts += 1
        else:
            updated_accounts += 1
        account.available_funds = float(account_data.available_funds or 0)
        account.enabled = bool(account_data.enabled)

        existing_positions = {
            (position.stock.market, position.stock.symbol): position
            for position in db.query(Position)
            .filter(Position.account_id == account.id)
            .all()
            if position.stock
        }
        desired_keys = {
            (position.market, position.symbol)
            for position in account_data.positions or []
        }
        if mode == "replace":
            for stock_key, position in existing_positions.items():
                if stock_key not in desired_keys:
                    db.delete(position)

        for position_data in account_data.positions or []:
            stock = (
                db.query(Stock)
                .filter(
                    Stock.symbol == position_data.symbol,
                    Stock.market == position_data.market,
                )
                .first()
            )
            if not stock:
                stock = Stock(
                    symbol=position_data.symbol,
                    name=position_data.name,
                    market=position_data.market,
                )
                db.add(stock)
                db.flush()
                created_stocks += 1
            elif position_data.name:
                stock.name = position_data.name

            position_key = (position_data.market, position_data.symbol)
            position = existing_positions.get(position_key)
            if not position:
                position = Position(account_id=account.id, stock_id=stock.id)
                db.add(position)
                created_positions += 1
            else:
                updated_positions += 1
            position.cost_price = float(position_data.cost_price)
            position.quantity = int(position_data.quantity)
            position.invested_amount = position_data.invested_amount
            position.sort_order = int(position_data.sort_order or 0)
            position.trading_style = position_data.trading_style or "swing"

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning("配置包导入因无效关联回滚: %s", exc)
        raise api_error(400, "template_reference_invalid", "配置包包含无效关联，导入已回滚") from exc
    logger.info(
        f"导入配置包 modules={','.join(sorted(selected))}: settings={updated_settings} "
        f"ai_services(+{created_ai_services}/~{updated_ai_services}) "
        f"ai_models(+{created_ai_models}/~{updated_ai_models}) "
        f"channels(+{created_notify_channels}/~{updated_notify_channels}) "
        f"agents(+{created_agents}/~{updated_agents}) "
        f"stocks(+{created_stocks}/~{updated_stocks}) "
        f"stock_agents(+{created_stock_agents}/~{updated_stock_agents}) "
        f"accounts(+{created_accounts}/~{updated_accounts}) "
        f"positions(+{created_positions}/~{updated_positions}) "
        f"filtered_refs(model={dropped_ai_model_refs},channel={dropped_notify_channel_refs})"
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
        "modules": [module for module in _MODULE_ORDER if module in selected],
        "scheduler_reloaded": reloaded,
        "summary": {
            "updated_settings": updated_settings,
            "created_ai_services": created_ai_services,
            "updated_ai_services": updated_ai_services,
            "created_ai_models": created_ai_models,
            "updated_ai_models": updated_ai_models,
            "created_notify_channels": created_notify_channels,
            "updated_notify_channels": updated_notify_channels,
            "created_agents": created_agents,
            "updated_agents": updated_agents,
            "created_stocks": created_stocks,
            "updated_stocks": updated_stocks,
            "created_stock_agents": created_stock_agents,
            "updated_stock_agents": updated_stock_agents,
            "created_accounts": created_accounts,
            "updated_accounts": updated_accounts,
            "created_positions": created_positions,
            "updated_positions": updated_positions,
            "dropped_ai_model_refs": dropped_ai_model_refs,
            "dropped_notify_channel_refs": dropped_notify_channel_refs,
        },
        "warnings": warnings,
    }
