"""MCP JSON-RPC API（HTTP 传输）

提供完整的 MCP 能力：
- initialize
- tools/list
- tools/call

支持两种认证方式：
- Bearer（复用已登录 token）
- Basic（用户名/密码）
"""

import asyncio
import json
import secrets
import base64
import binascii
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any
import jwt
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

from src.collectors.akshare_collector import (
    _fetch_tencent_quotes,
    _tencent_symbol,
)
from src.collectors.etf_collector import (
    get_etf_holdings,
    get_etf_overview,
    get_etf_spot,
)
from src.collectors.kline_collector import KlineCollector
from src.collectors.news_collector import NewsCollector
from src.web.api.accounts import (
    PositionAddRequest,
    PositionReduceRequest,
    add_to_position,
    reduce_from_position,
    get_hkd_cny_rate,
    get_portfolio_summary,
    get_usd_cny_rate,
)
from src.web.api.dashboard import get_dashboard_overview
from src.web.api.auth import (
    ENV_AUTH_PASSWORD,
    ENV_AUTH_USERNAME,
    JWT_ALGORITHM,
    get_password_hash,
    get_stored_username,
    get_jwt_secret,
    hash_password,
)
from src.web.database import get_db
from src.web.stock_list import search_stocks
from src.web.api.market import MARKET_INDICES
from src.models.market import MarketCode
from src.web.models import (
    Account,
    Position,
    PositionTrade,
    Stock,
    StockAgent,
    LogEntry,
    AgentConfig,
    AgentRun,
    AnalysisHistory,
    PriceAlertRule,
    PriceAlertHit,
)
from src.core.suggestion_pool import get_suggestions_for_stock, get_latest_suggestions
from src.core.agent_catalog import (
    AGENT_KIND_CAPABILITY,
    AGENT_KIND_WORKFLOW,
    CAPABILITY_AGENT_NAMES,
    infer_agent_kind,
)
from src.core.schedule_parser import preview_schedule, count_runs_within
from src.core.price_alert_engine import ENGINE
from src.core.json_safe import to_jsonable
from src.config import Settings

router = APIRouter()
logger = logging.getLogger(__name__)

WRITE_TOOL_NAMES = {
    "positions.create",
    "positions.update",
    "positions.trade",
    "positions.delete",
    "positions.reorder.batch",
    "stocks.create",
    "stocks.update",
    "stocks.delete",
    "stocks.reorder",
    "accounts.create",
    "accounts.update",
    "accounts.delete",
    "price_alerts.create",
    "price_alerts.update",
    "price_alerts.delete",
    "price_alerts.toggle",
    "agents.trigger",
}

MCP_SERVER_VERSION = "0.3.1"

ERR_INVALID_PARAMS = "MCP_INVALID_PARAMS"
ERR_AUTH_FAILED = "MCP_AUTH_FAILED"
ERR_NOT_FOUND = "MCP_RESOURCE_NOT_FOUND"
ERR_CONFLICT = "MCP_RESOURCE_CONFLICT"
ERR_INTERNAL = "MCP_INTERNAL_ERROR"


class McpToolError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        hint: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}


def _tool_meta(tool: dict[str, Any], *, tags: list[str], risk_level: str, cost_hint: str) -> dict[str, Any]:
    out = dict(tool)
    out["tags"] = tags
    out["risk_level"] = risk_level
    out["cost_hint"] = cost_hint
    return out


def _build_error_data(
    *,
    error_code: str,
    hint: str,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "error_code": error_code,
        "hint": hint,
        "retryable": retryable,
    }
    if details:
        payload["details"] = details
    return payload


def _audit_write_tool(
    *,
    principal: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
    status_text: str,
    duration_ms: int,
) -> None:
    safe_args = {
        k: v for k, v in arguments.items() if k not in {"password", "token", "authorization"}
    }
    mcp_tags = {
        "mcp": {
            "tool_name": tool_name,
            "status": status_text,
            "user": str(principal.get("user") or ""),
            "auth": str(principal.get("auth") or ""),
            "permission": str(principal.get("permission") or ""),
            "duration_ms": int(duration_ms),
            "arguments": safe_args,
        }
    }
    logger.info(
        "[mcp.audit] user=%s auth=%s perm=%s tool=%s status=%s duration_ms=%s args=%s",
        principal.get("user"),
        principal.get("auth"),
        principal.get("permission"),
        tool_name,
        status_text,
        duration_ms,
        safe_args,
        extra={
            "event": "mcp.audit",
            "agent_name": "mcp",
            "tags": mcp_tags,
        },
    )


def _mcp_logs_query(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    level = str(arguments.get("level", "")).strip().upper()
    q = str(arguments.get("q", "")).strip()
    tool_name = str(arguments.get("tool_name", "")).strip()
    status_text = str(arguments.get("status", "")).strip()
    user = str(arguments.get("user", "")).strip()
    auth = str(arguments.get("auth", "")).strip().lower()
    limit = max(1, min(200, int(arguments.get("limit", 50))))
    before_id = max(0, int(arguments.get("before_id", 0)))

    query = db.query(LogEntry).filter(LogEntry.event == "mcp.audit")

    if level:
        levels = [it.strip().upper() for it in level.split(",") if it.strip()]
        if levels:
            query = query.filter(LogEntry.level.in_(levels))
    if q:
        query = query.filter(
            or_(
                LogEntry.message.contains(q),
                LogEntry.logger_name.contains(q),
                LogEntry.event.contains(q),
            )
        )
    if tool_name:
        query = query.filter(
            or_(
                func.json_extract(
                    LogEntry.tags, "$.mcp.tool_name") == tool_name,
                LogEntry.message.contains(f"tool={tool_name}"),
            )
        )
    if status_text:
        query = query.filter(
            or_(
                func.json_extract(
                    LogEntry.tags, "$.mcp.status") == status_text,
                LogEntry.message.contains(f"status={status_text}"),
            )
        )
    if user:
        query = query.filter(
            or_(
                func.json_extract(LogEntry.tags, "$.mcp.user") == user,
                LogEntry.message.contains(f"user={user}"),
            )
        )
    if auth:
        query = query.filter(
            or_(
                func.lower(func.json_extract(
                    LogEntry.tags, "$.mcp.auth")) == auth,
                LogEntry.message.contains(f"auth={auth}"),
            )
        )

    if before_id > 0:
        rows = (
            query.filter(LogEntry.id < before_id)
            .order_by(LogEntry.id.desc())
            .limit(limit + 1)
            .all()
        )
    else:
        rows = query.order_by(LogEntry.id.desc()).limit(limit + 1).all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_before_id = rows[-1].id if rows else None

    items: list[dict[str, Any]] = []
    for row in rows:
        tags = row.tags if isinstance(row.tags, dict) else {}
        mcp_meta = tags.get("mcp", {}) if isinstance(tags, dict) else {}
        ts = row.timestamp
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        items.append(
            {
                "id": row.id,
                "timestamp": ts.isoformat() if ts else "",
                "level": row.level,
                "tool_name": str(mcp_meta.get("tool_name") or ""),
                "status": str(mcp_meta.get("status") or ""),
                "user": str(mcp_meta.get("user") or ""),
                "auth": str(mcp_meta.get("auth") or ""),
                "duration_ms": int(mcp_meta.get("duration_ms") or 0),
                "arguments": mcp_meta.get("arguments") if isinstance(mcp_meta.get("arguments"), dict) else {},
                "message": row.message or "",
            }
        )

    return {
        "items": items,
        "count": len(items),
        "has_more": has_more,
        "next_before_id": next_before_id,
    }


def _jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        payload["error"]["data"] = data
    return payload


def _mcp_content(payload: Any) -> dict[str, Any]:
    safe_payload = to_jsonable(payload)
    text = json.dumps(safe_payload, ensure_ascii=False)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": safe_payload,
    }


def _resolve_mcp_auth_config(db: Session) -> tuple[str | None, str | None]:
    """解析 MCP 登录凭据（环境变量优先，否则读取应用认证配置）。"""
    if ENV_AUTH_USERNAME and ENV_AUTH_PASSWORD:
        return ENV_AUTH_USERNAME, hash_password(ENV_AUTH_PASSWORD)

    username = get_stored_username(db)
    password_hash = get_password_hash(db)
    if username and password_hash:
        return username, password_hash
    return None, None


def _parse_basic_auth(raw_value: str) -> tuple[str, str] | None:
    if not raw_value or not raw_value.startswith("Basic "):
        return None
    encoded = raw_value[6:].strip()
    if not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    username, password = decoded.split(":", 1)
    return username, password


def _decode_bearer_payload(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, get_jwt_secret(),
                             algorithms=[JWT_ALGORITHM])
        if isinstance(payload, dict):
            return payload
        return None
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def require_mcp_user(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """MCP 认证：优先允许 Bearer（已登录态），其次 Basic（用户名/密码）。"""
    realm = "Basic realm=AlphaMind-MCP"
    auth_value = request.headers.get("authorization", "")

    if auth_value.startswith("Bearer "):
        token = auth_value[7:].strip()
        payload = _decode_bearer_payload(token) if token else None
        if payload is not None:
            return {
                "user": str(payload.get("sub") or "token-user"),
                "auth": "bearer",
                "read_only": False,
                "permission": "rw",
            }
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP 登录态无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    basic_user_pass = _parse_basic_auth(auth_value)
    if not basic_user_pass:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP 需要用户名密码，或使用已登录 token",
            headers={"WWW-Authenticate": "Bearer, Basic realm=AlphaMind-MCP"},
        )

    username, password = basic_user_pass
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP 需要用户名和密码",
            headers={"WWW-Authenticate": realm},
        )

    expected_username, expected_hash = _resolve_mcp_auth_config(db)
    if not expected_username or not expected_hash:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP 认证未初始化，请先在系统中设置用户名密码",
        )

    is_user_ok = secrets.compare_digest(username, expected_username)
    is_pass_ok = secrets.compare_digest(hash_password(password), expected_hash)
    if not (is_user_ok and is_pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP 用户名或密码错误",
            headers={"WWW-Authenticate": realm},
        )
    return {
        "user": username,
        "auth": "basic",
        "read_only": False,
        "permission": "rw",
    }


def _position_to_dict(position: Position) -> dict[str, Any]:
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": position.account.name if position.account else None,
        "stock_symbol": position.stock.symbol if position.stock else None,
        "stock_name": position.stock.name if position.stock else None,
    }


def _position_trade_to_dict(row: PositionTrade) -> dict[str, Any]:
    return {
        "id": row.id,
        "position_id": row.position_id,
        "side": row.side,
        "quantity": row.quantity,
        "price": row.price,
        "amount": row.amount,
        "cost_before": row.cost_before,
        "qty_before": row.qty_before,
        "cost_after": row.cost_after,
        "qty_after": row.qty_after,
        "note": row.note,
        "traded_at": row.traded_at.isoformat() if row.traded_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "account_name": row.position.account.name if row.position and row.position.account else None,
        "stock_symbol": row.position.stock.symbol if row.position and row.position.stock else None,
        "stock_name": row.position.stock.name if row.position and row.position.stock else None,
    }


def _require_args(arguments: dict[str, Any], fields: list[str]) -> None:
    for field in fields:
        if field not in arguments:
            raise McpToolError(
                error_code=ERR_INVALID_PARAMS,
                message=f"缺少必填参数: {field}",
                hint="请根据 tools/list 中 inputSchema.required 补齐参数。",
                retryable=False,
                details={"missing_field": field},
            )


def _pagination_args(arguments: dict[str, Any]) -> tuple[int, int, int]:
    page = int(arguments.get("page", 1) or 1)
    page_size = int(arguments.get("page_size", 50) or 50)
    since_id = int(arguments.get("since_id", 0) or 0)
    if page < 1:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message="page 必须 >= 1",
            hint="将 page 设置为 1 或更大整数。",
        )
    if page_size < 1 or page_size > 200:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message="page_size 必须在 1-200 之间",
            hint="建议 page_size 使用 20-100。",
        )
    if since_id < 0:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message="since_id 必须 >= 0",
            hint="不做增量时可传 0。",
        )
    return page, page_size, since_id


def _list_positions(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    account_id = arguments.get("account_id")
    stock_id = arguments.get("stock_id")
    page, page_size, since_id = _pagination_args(arguments)

    query = db.query(Position)
    if account_id is not None:
        query = query.filter(Position.account_id == int(account_id))
    if stock_id is not None:
        query = query.filter(Position.stock_id == int(stock_id))
    if since_id > 0:
        query = query.filter(Position.id > since_id)

    total = query.count()
    rows = query.order_by(Position.account_id.asc(), Position.sort_order.asc(), Position.id.asc()) \
        .offset((page - 1) * page_size).limit(page_size).all()
    max_id = max([int(row.id) for row in rows], default=since_id)
    return {
        "items": [_position_to_dict(row) for row in rows],
        "count": len(rows),
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < int(total),
        "next_since_id": max_id,
    }


def _create_position(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["account_id",
                  "stock_id", "cost_price", "quantity"])
    account_id = int(arguments["account_id"])
    stock_id = int(arguments["stock_id"])

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="账户不存在",
            hint="请先通过账户接口创建账户后再创建持仓。",
        )

    stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not stock:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="股票不存在",
            hint="请先确认 stock_id 对应自选股存在。",
        )

    existing = db.query(Position).filter(
        Position.account_id == account_id,
        Position.stock_id == stock_id,
    ).first()
    if existing:
        raise McpToolError(
            error_code=ERR_CONFLICT,
            message=f"账户 {account.name} 已有 {stock.name} 的持仓，请编辑现有持仓",
            hint="调用 positions.update 更新现有记录。",
        )

    max_order = db.query(func.max(Position.sort_order)).filter(
        Position.account_id == account_id
    ).scalar() or 0

    quantity = int(arguments["quantity"])
    cost = float(arguments["cost_price"])

    position = Position(
        account_id=account_id,
        stock_id=stock_id,
        cost_price=cost,
        quantity=quantity,
        invested_amount=arguments.get("invested_amount"),
        sort_order=int(max_order) + 1,
        trading_style=arguments.get("trading_style"),
    )
    db.add(position)
    db.flush()

    # 与 Web API 保持一致：创建持仓时自动写入一条初始建仓流水。
    trade = PositionTrade(
        position_id=position.id,
        side="buy",
        price=cost,
        quantity=quantity,
        amount=round(cost * quantity, 4),
        cost_before=None,
        qty_before=None,
        cost_after=cost,
        qty_after=quantity,
        note="建仓",
        traded_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(trade)
    db.commit()
    db.refresh(position)
    return _position_to_dict(position)


def _update_position(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["position_id"])
    position_id = int(arguments["position_id"])
    row = db.query(Position).filter(Position.id == position_id).first()
    if not row:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="持仓不存在",
            hint="请检查 position_id 是否正确。",
        )

    if "cost_price" in arguments:
        row.cost_price = float(arguments["cost_price"])
    if "quantity" in arguments:
        row.quantity = int(arguments["quantity"])
    if "invested_amount" in arguments:
        row.invested_amount = arguments["invested_amount"]
    if "trading_style" in arguments:
        trading_style = arguments["trading_style"]
        row.trading_style = trading_style if trading_style else None

    db.commit()
    db.refresh(row)
    return _position_to_dict(row)


def _trade_position(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["position_id", "action", "quantity", "price"])
    position_id = int(arguments["position_id"])
    row = db.query(Position).filter(Position.id == position_id).first()
    if not row:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="持仓不存在",
            hint="请检查 position_id 是否正确。",
        )

    action = str(arguments.get("action") or "").strip().lower()
    quantity = int(arguments.get("quantity") or 0)
    price = float(arguments.get("price") or 0)
    note = str(arguments.get("note") or "").strip() or None
    traded_at_raw = arguments.get("trade_date")
    traded_at = None
    if traded_at_raw:
        try:
            traded_at = datetime.strptime(str(traded_at_raw), "%Y-%m-%d")
        except ValueError:
            raise McpToolError(
                error_code=ERR_INVALID_PARAMS,
                message="trade_date 格式应为 YYYY-MM-DD",
                hint="例如 2026-06-25；不传则使用当前时间。",
            )

    try:
        if action == "add":
            payload = PositionAddRequest(
                price=price, quantity=quantity, note=note, traded_at=traded_at
            )
            result = add_to_position(position_id, payload, db)
        elif action == "reduce":
            payload = PositionReduceRequest(
                price=price, quantity=quantity, note=note, traded_at=traded_at
            )
            result = reduce_from_position(position_id, payload, db)
        else:
            raise McpToolError(
                error_code=ERR_INVALID_PARAMS,
                message=f"不支持的动作: {action}",
                hint="action 仅支持 add（加仓）或 reduce（减仓）。",
            )
    except HTTPException as exc:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message=str(exc.detail),
            hint="请检查 action/quantity/price 是否合法（卖出不能超过持仓）。",
        ) from exc

    trade = result.get("trade") if isinstance(result, dict) else None
    if trade is None:
        return _position_to_dict(row)
    # add/reduce 返回的 trade 是已序列化的 dict，补齐关联字段后直接返回。
    trade_dict = dict(trade)
    trade_dict.setdefault(
        "account_name", row.account.name if row.account else None
    )
    trade_dict.setdefault(
        "stock_symbol", row.stock.symbol if row.stock else None
    )
    trade_dict.setdefault(
        "stock_name", row.stock.name if row.stock else None
    )
    return trade_dict


def _list_position_trades(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["position_id"])
    position_id = int(arguments["position_id"])
    page = int(arguments.get("page", 1) or 1)
    page_size = int(arguments.get("page_size", 5) or 5)
    current_page = max(1, page)
    size = max(1, min(page_size, 200))

    query = db.query(PositionTrade).filter(
        PositionTrade.position_id == position_id
    )
    total = int(query.count())
    rows = query.order_by(PositionTrade.id.desc()).offset(
        (current_page - 1) * size).limit(size).all()
    return {
        "items": [_position_trade_to_dict(row) for row in rows],
        "count": len(rows),
        "total": total,
        "page": current_page,
        "page_size": size,
        "has_more": current_page * size < total,
    }


def _delete_position(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["position_id"])
    position_id = int(arguments["position_id"])
    row = db.query(Position).filter(Position.id == position_id).first()
    if not row:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="持仓不存在",
            hint="请检查 position_id 是否正确。",
        )

    db.delete(row)
    db.commit()
    return {"success": True, "position_id": position_id}


def _reorder_positions(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    items = arguments.get("items")
    if not isinstance(items, list) or not items:
        return {"updated": 0}

    ids = [int(item["id"])
           for item in items if "id" in item and "sort_order" in item]
    rows = db.query(Position).filter(Position.id.in_(ids)).all()
    row_map = {row.id: row for row in rows}

    updated = 0
    for item in items:
        if "id" not in item or "sort_order" not in item:
            continue
        row = row_map.get(int(item["id"]))
        if not row:
            continue
        row.sort_order = int(item["sort_order"])
        updated += 1

    db.commit()
    return {"updated": updated}


def _portfolio_summary(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    account_id = arguments.get("account_id")
    include_quotes = bool(arguments.get("include_quotes", False))
    return get_portfolio_summary(
        account_id=int(account_id) if account_id is not None else None,
        include_quotes=include_quotes,
        db=db,
    )


def _list_watchlist(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    page, page_size, since_id = _pagination_args(arguments)
    query = db.query(Stock)
    if since_id > 0:
        query = query.filter(Stock.id > since_id)

    total = query.count()
    rows = query.order_by(
        Stock.is_featured.desc(),
        Stock.sort_order.asc(),
        Stock.id.asc(),
    ).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": row.id,
            "symbol": row.symbol,
            "name": row.name,
            "market": row.market,
            "sort_order": row.sort_order or 0,
            "is_featured": bool(row.is_featured),
        }
        for row in rows
    ]
    max_id = max([int(row["id"]) for row in items], default=since_id)
    return {
        "items": items,
        "count": len(items),
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < int(total),
        "next_since_id": max_id,
    }


def _watchlist_quotes(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = arguments
    rows = db.query(Stock).all()
    if not rows:
        return {"quotes": {}}

    market_stocks: dict[str, list[Stock]] = {}
    for stock in rows:
        market_stocks.setdefault(stock.market, []).append(stock)

    quotes: dict[str, Any] = {}
    for market, stock_list in market_stocks.items():
        try:
            market_code = MarketCode(market)
        except ValueError:
            continue

        symbols = [_tencent_symbol(stock.symbol, market_code)
                   for stock in stock_list]
        try:
            items = _fetch_tencent_quotes(symbols)
            for item in items:
                quotes[item["symbol"]] = {
                    "current_price": item["current_price"],
                    "change_pct": item["change_pct"],
                    "change_amount": item["change_amount"],
                    "prev_close": item["prev_close"],
                }
        except Exception:
            continue

    return {"quotes": quotes}


def _market_indices(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = arguments
    _ = db
    # 仅取有腾讯代码的指数（部分 GLOBAL 指数只有东财 secid，走不到腾讯行情）
    tencent_indices = [idx for idx in MARKET_INDICES if idx.get("tencent_symbol")]
    tencent_symbols = [idx["tencent_symbol"] for idx in tencent_indices]
    try:
        quotes = _fetch_tencent_quotes(tencent_symbols)
    except Exception:
        quotes = []

    quote_map = {item["symbol"]: item for item in quotes}
    result = []
    for idx in MARKET_INDICES:
        quote = quote_map.get(idx.get("response_symbol", ""))
        result.append(
            {
                "symbol": idx["symbol"],
                "name": idx["name"],
                "market": idx["market"],
                "current_price": quote["current_price"] if quote else None,
                "change_pct": quote["change_pct"] if quote else None,
                "change_amount": quote["change_amount"] if quote else None,
                "prev_close": quote["prev_close"] if quote else None,
            }
        )
    return {"items": result}


def _dashboard_overview(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    market = str(arguments.get("market", "ALL"))
    action_limit = int(arguments.get("action_limit", 6))
    risk_limit = int(arguments.get("risk_limit", 6))
    days = int(arguments.get("days", 45))
    lite = bool(arguments.get("lite", False))
    fields = arguments.get("fields")

    raw = get_dashboard_overview(
        market=market,
        action_limit=action_limit,
        risk_limit=risk_limit,
        days=days,
        db=db,
    )

    if lite:
        raw = {
            "generated_at": raw.get("generated_at"),
            "market": raw.get("market"),
            "snapshot_date": raw.get("snapshot_date"),
            "kpis": raw.get("kpis"),
            "portfolio": raw.get("portfolio"),
        }

    if fields:
        if isinstance(fields, str):
            selected = {x.strip() for x in fields.split(",") if x.strip()}
        elif isinstance(fields, list):
            selected = {str(x).strip() for x in fields if str(x).strip()}
        else:
            raise McpToolError(
                error_code=ERR_INVALID_PARAMS,
                message="fields 参数必须是字符串或字符串数组",
                hint="例如 fields='kpis,portfolio' 或 fields=['kpis','portfolio']",
            )

        required = {"generated_at", "market"}
        selected = selected.union(required)
        raw = {k: v for k, v in raw.items() if k in selected}

    return raw


def _mcp_health(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = arguments
    _ = db
    return {
        "status": "ok",
        "server": "panwatch-mcp",
        "version": MCP_SERVER_VERSION,
        "time_ms": int(time.time() * 1000),
    }


def _mcp_auth_status(arguments: dict[str, Any], principal: dict[str, Any]) -> dict[str, Any]:
    _ = arguments
    return {
        "user": principal.get("user"),
        "auth": principal.get("auth"),
    }


def _mcp_version(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = arguments
    _ = db
    return {
        "name": "panwatch-mcp",
        "version": MCP_SERVER_VERSION,
        "protocolVersion": "2024-11-05",
    }


# ==================== 自选股管理 ====================

def _stock_to_dict(stock: Stock) -> dict[str, Any]:
    return {
        "id": stock.id,
        "symbol": stock.symbol,
        "name": stock.name,
        "market": stock.market,
        "sort_order": stock.sort_order or 0,
        "is_featured": bool(stock.is_featured),
    }


def _create_stock(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["symbol", "name"])
    symbol = str(arguments["symbol"]).strip().upper()
    name = str(arguments["name"]).strip()
    market = str(arguments.get("market", "CN")).strip().upper()

    if market not in ("CN", "HK", "US", "FUND"):
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message=f"不支持的市场: {market}",
            hint="市场类型仅支持 CN/HK/US/FUND",
        )

    existing = db.query(Stock).filter(
        Stock.symbol == symbol, Stock.market == market
    ).first()
    if existing:
        raise McpToolError(
            error_code=ERR_CONFLICT,
            message=f"股票 {symbol} ({market}) 已存在",
            hint="请使用 stocks.update 更新现有记录",
            details={"stock_id": existing.id},
        )

    max_order = db.query(func.max(Stock.sort_order)).scalar() or 0
    stock = Stock(symbol=symbol, name=name, market=market,
                  sort_order=int(max_order) + 1)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return _stock_to_dict(stock)


def _update_stock(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["stock_id"])
    stock_id = int(arguments["stock_id"])
    stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not stock:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="股票不存在",
            hint="请检查 stock_id 是否正确",
        )

    if "name" in arguments:
        stock.name = str(arguments["name"]).strip()

    db.commit()
    db.refresh(stock)
    return _stock_to_dict(stock)


def _delete_stock(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["stock_id"])
    stock_id = int(arguments["stock_id"])
    stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not stock:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="股票不存在",
            hint="请检查 stock_id 是否正确",
        )

    has_position = db.query(Position.id).filter(
        Position.stock_id == stock_id).first()
    if has_position:
        raise McpToolError(
            error_code=ERR_CONFLICT,
            message="该股票存在持仓，请先删除持仓后再删除股票",
            hint="调用 positions.delete 先删除持仓",
        )

    # 清理关联数据
    rule_ids = [r[0] for r in db.query(PriceAlertRule.id).filter(
        PriceAlertRule.stock_id == stock_id).all()]
    if rule_ids:
        db.query(PriceAlertHit).filter(PriceAlertHit.rule_id.in_(rule_ids)).delete(
            synchronize_session=False)
    db.query(PriceAlertHit).filter(PriceAlertHit.stock_id == stock_id).delete(
        synchronize_session=False)
    db.query(PriceAlertRule).filter(PriceAlertRule.stock_id == stock_id).delete(
        synchronize_session=False)
    db.query(StockAgent).filter(StockAgent.stock_id == stock_id).delete(
        synchronize_session=False)

    db.delete(stock)
    db.commit()
    return {"success": True, "stock_id": stock_id}


def _search_stocks(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = db
    query = str(arguments.get("query", "")).strip()
    market = str(arguments.get("market", "")).strip().upper()
    limit = int(arguments.get("limit", 20))

    if not query:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message="搜索关键词不能为空",
            hint="请提供 query 参数",
        )

    results = search_stocks(query, market)
    return {
        "items": results[:limit],
        "count": len(results[:limit]),
    }


def _resolve_stock(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["symbol"])
    symbol_raw = str(arguments["symbol"]).strip().upper()
    market_raw = arguments.get("market")
    market = str(market_raw).strip().upper() if market_raw is not None else ""

    def _normalize_symbol(code: str, mkt: str) -> str:
        c = (code or "").strip().upper()
        for p in ("SH", "SZ", "BJ", "US", "HK"):
            if c.startswith(p):
                c = c[len(p):]
                break
        if "." in c:
            c = c.split(".")[0]
        if c.isdigit():
            if mkt in ("CN", "FUND"):
                c = c.zfill(6)
            elif mkt == "HK":
                c = c.zfill(5)
        return c

    symbol = _normalize_symbol(symbol_raw, market)

    def _guess_market_priority(sym: str) -> list[str]:
        if not sym:
            return ["CN", "FUND", "HK", "US"]
        if sym.isalpha():
            return ["US", "HK", "CN", "FUND"]
        if sym.isdigit():
            if len(sym) == 5:
                return ["HK", "CN", "FUND", "US"]
            if len(sym) == 6:
                fund_prefixes = ("15", "16", "50", "51",
                                 "52", "56", "58", "59")
                cn_stock_prefixes = ("00", "30", "60", "68",
                                     "83", "87", "43", "92")
                if sym.startswith(fund_prefixes):
                    return ["FUND", "CN", "HK", "US"]
                if sym.startswith(cn_stock_prefixes):
                    return ["CN", "FUND", "HK", "US"]
                return ["CN", "FUND", "HK", "US"]
        return ["CN", "FUND", "HK", "US"]

    symbol_candidates = {symbol}
    # market 未指定时，补齐常见数字代码位数，容忍外部调用传入丢前导零。
    if not market and symbol.isdigit() and len(symbol) <= 6:
        symbol_candidates.add(symbol.zfill(6))
        symbol_candidates.add(symbol.zfill(5))

    query = db.query(Stock).filter(Stock.symbol.in_(list(symbol_candidates)))
    if market:
        if market not in ("CN", "HK", "US", "FUND"):
            raise McpToolError(
                error_code=ERR_INVALID_PARAMS,
                message=f"不支持的市场: {market}",
                hint="市场类型仅支持 CN/HK/US/FUND",
            )
        query = query.filter(Stock.market == market)

    rows = query.order_by(Stock.id.asc()).all()

    # A 股 ETF 常被归类为 FUND，兼容 market=CN/FUND 的解析。
    if not rows and market in ("CN", "FUND"):
        fallback_markets = ["CN", "FUND"]
        rows = db.query(Stock).filter(
            Stock.symbol.in_(list(symbol_candidates)),
            Stock.market.in_(fallback_markets),
        ).order_by(Stock.id.asc()).all()

    # market 未传时，按代码形态自动推断优先级并返回最佳匹配，降低调用参数复杂度。
    if len(rows) > 1 and not market:
        market_priority = _guess_market_priority(symbol)
        by_market: dict[str, list[Stock]] = {}
        for r in rows:
            by_market.setdefault(str(r.market).upper(), []).append(r)
        for mkt in market_priority:
            if by_market.get(mkt):
                rows = [sorted(by_market[mkt], key=lambda x: x.id)[0]]
                break

    # 若自选股中不存在，尝试从股票搜索结果精确命中后自动补建，便于直接拿 stock_id 落库持仓。
    if not rows:
        search_markets = [market] if market else _guess_market_priority(symbol)
        if market in ("CN", "FUND"):
            search_markets = ["CN", "FUND"]

        matched_candidate: dict[str, Any] | None = None
        for mkt in search_markets:
            candidates = search_stocks(symbol, mkt, limit=50)
            for c in candidates:
                c_symbol = _normalize_symbol(
                    str(c.get("symbol", "")), str(c.get("market", "")).upper())
                c_market = str(c.get("market", "")).upper()
                if c_symbol in symbol_candidates and (not market or c_market == market or (market in ("CN", "FUND") and c_market in ("CN", "FUND"))):
                    matched_candidate = c
                    break
            if matched_candidate:
                break

        if matched_candidate:
            cand_symbol = _normalize_symbol(
                str(matched_candidate.get("symbol", "")),
                str(matched_candidate.get("market", "")).upper(),
            )
            cand_market = str(matched_candidate.get(
                "market", "")).strip().upper() or (market or "CN")
            existing = db.query(Stock).filter(
                Stock.symbol == cand_symbol,
                Stock.market == cand_market,
            ).first()
            if existing:
                rows = [existing]
            else:
                max_order = db.query(func.max(Stock.sort_order)).scalar() or 0
                created = Stock(
                    symbol=cand_symbol,
                    name=str(matched_candidate.get("name", "")
                             ).strip() or cand_symbol,
                    market=cand_market,
                    sort_order=int(max_order) + 1,
                )
                db.add(created)
                db.commit()
                db.refresh(created)
                rows = [created]

    if not rows:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="未找到对应股票",
            hint="请先调用 stocks.create 添加到自选股，或检查 symbol/market 是否正确。",
            details={"symbol": symbol, "market": market or None},
        )

    if len(rows) > 1 and not market:
        # 理论上已在上方按优先级收敛；兜底保持可解析，避免调用方因缺省 market 失败。
        rows = [rows[0]]

    stock = rows[0]
    return {
        "resolved": True,
        "stock_id": stock.id,
        "symbol": stock.symbol,
        "name": stock.name,
        "market": stock.market,
    }


def _reorder_stocks(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    items = arguments.get("items")
    if not isinstance(items, list) or not items:
        return {"updated": 0}

    ids = [int(item["id"])
           for item in items if "id" in item and "sort_order" in item]
    rows = db.query(Stock).filter(Stock.id.in_(ids)).all()
    row_map = {row.id: row for row in rows}

    updated = 0
    for item in items:
        if "id" not in item or "sort_order" not in item:
            continue
        row = row_map.get(int(item["id"]))
        if not row:
            continue
        row.sort_order = int(item["sort_order"])
        updated += 1

    db.commit()
    return {"updated": updated}


# ==================== 账户管理 ====================

_SUPPORTED_ACCOUNT_MARKETS = {"CN", "HK", "US", "FUND"}
_SUPPORTED_ACCOUNT_CURRENCIES = {"CNY", "HKD", "USD"}
_MARKET_CURRENCY_MAP = {
    "CN": "CNY",
    "HK": "HKD",
    "US": "USD",
    "FUND": "CNY",
}


def _validate_account_market_currency(market: str, base_currency: str) -> None:
    expected = _MARKET_CURRENCY_MAP.get(market)
    if expected and base_currency != expected:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message=f"{market} 账户的 base_currency 需为 {expected}",
            hint=f"请传入 base_currency={expected}",
        )


def _account_to_dict(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "name": account.name,
        "market": str(getattr(account, "market", "CN") or "CN").upper(),
        "base_currency": str(getattr(account, "base_currency", "CNY") or "CNY").upper(),
        "available_funds": account.available_funds,
        "other_funds": float(getattr(account, "other_funds", 0) or 0),
        "other_fund_items": list(getattr(account, "other_fund_items", None) or []),
        "initial_funds": float(getattr(account, "initial_funds", 0) or 0),
        "enabled": account.enabled,
    }


def _list_accounts(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = arguments
    rows = db.query(Account).order_by(Account.id).all()
    return {
        "items": [_account_to_dict(a) for a in rows],
        "count": len(rows),
    }


def _create_account(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["name"])
    name = str(arguments["name"]).strip()
    available_funds = float(arguments.get("available_funds", 0) or 0)
    other_funds = float(arguments.get("other_funds", 0) or 0)
    raw_other_items = arguments.get("other_fund_items")
    from src.web.api.accounts import _normalize_other_fund_items, _sum_other_fund_items

    other_items = _normalize_other_fund_items(
        raw_other_items if isinstance(raw_other_items, list) else None,
        other_funds if raw_other_items is None else None,
    )
    other_total = _sum_other_fund_items(other_items)
    market = str(arguments.get("market", "CN") or "CN").upper()
    base_currency = str(arguments.get("base_currency", "CNY") or "CNY").upper()
    if market not in _SUPPORTED_ACCOUNT_MARKETS:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message="market 仅支持 CN/HK/US/FUND",
            hint="请传入 market=CN/HK/US/FUND",
        )
    if base_currency not in _SUPPORTED_ACCOUNT_CURRENCIES:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message="base_currency 仅支持 CNY/HKD/USD",
            hint="请传入 base_currency=CNY/HKD/USD",
        )
    _validate_account_market_currency(market, base_currency)

    account = Account(
        name=name,
        available_funds=available_funds,
        other_funds=other_total,
        other_fund_items=other_items,
        initial_funds=0,
        base_currency=base_currency,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    from src.web.api.accounts import _sync_account_initial_funds

    _sync_account_initial_funds(account, db)
    db.commit()
    db.refresh(account)
    return _account_to_dict(account)


def _update_account(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["account_id"])
    account_id = int(arguments["account_id"])
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="账户不存在",
            hint="请检查 account_id 是否正确",
        )

    if "name" in arguments:
        account.name = str(arguments["name"]).strip()
    if "available_funds" in arguments:
        account.available_funds = float(arguments["available_funds"])
    if "other_fund_items" in arguments and isinstance(arguments["other_fund_items"], list):
        from src.web.api.accounts import _apply_account_other_funds

        _apply_account_other_funds(account, arguments["other_fund_items"])
    elif "other_funds" in arguments:
        from src.web.api.accounts import _apply_account_other_funds

        _apply_account_other_funds(account, legacy_other_funds=float(arguments["other_funds"]))
    if "base_currency" in arguments:
        next_base_currency = str(arguments["base_currency"] or "CNY").upper()
        if next_base_currency not in _SUPPORTED_ACCOUNT_CURRENCIES:
            raise McpToolError(
                error_code=ERR_INVALID_PARAMS,
                message="base_currency 仅支持 CNY/HKD/USD",
                hint="请传入 base_currency=CNY/HKD/USD",
            )
        account.base_currency = next_base_currency
    if "enabled" in arguments:
        account.enabled = bool(arguments["enabled"])

    from src.web.api.accounts import _sync_account_initial_funds

    _sync_account_initial_funds(account, db)
    db.commit()
    db.refresh(account)
    return _account_to_dict(account)


def _delete_account(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["account_id"])
    account_id = int(arguments["account_id"])
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="账户不存在",
            hint="请检查 account_id 是否正确",
        )

    db.delete(account)
    db.commit()
    return {"success": True, "account_id": account_id}


# ==================== 新闻资讯 ====================

def _list_news(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    symbols = arguments.get("symbols")
    hours = int(arguments.get("hours", 168))
    limit = int(arguments.get("limit", 50))
    source_filter = arguments.get("source")

    symbol_list = None
    if symbols:
        if isinstance(symbols, str):
            symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
        elif isinstance(symbols, list):
            symbol_list = [str(s).strip() for s in symbols if str(s).strip()]

    if not symbol_list:
        # 获取所有自选股
        stocks = db.query(Stock).all()
        symbol_list = [s.symbol for s in stocks]

    if not symbol_list:
        return {"items": [], "count": 0}

    try:
        collector = NewsCollector.from_database()
        news_list = asyncio.get_event_loop().run_until_complete(
            collector.fetch_all(symbols=symbol_list, since_hours=hours)
        )
    except Exception as e:
        logger.warning(f"获取新闻失败: {e}")
        news_list = []

    # 过滤来源
    if source_filter:
        if isinstance(source_filter, str):
            sources = {s.strip()
                       for s in source_filter.split(",") if s.strip()}
        else:
            sources = {str(s).strip() for s in source_filter if str(s).strip()}
        news_list = [n for n in news_list if n.source in sources]

    # 限制数量
    news_list = news_list[:limit]

    items = [
        {
            "source": n.source,
            "title": n.title,
            "content": n.content or "",
            "publish_time": n.publish_time,
            "symbols": n.symbols or [],
            "importance": n.importance,
            "url": n.url or "",
        }
        for n in news_list
    ]
    return {"items": items, "count": len(items)}


# ==================== K线与技术指标 ====================

def _get_klines(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = db
    _require_args(arguments, ["symbol", "market"])
    symbol = str(arguments["symbol"]).strip()
    market = str(arguments["market"]).strip().upper()
    days = int(arguments.get("days", 60))
    # interval parameter reserved for future use; currently only daily is supported
    interval = str(arguments.get("interval", "1d"))

    if market == "FUND":
        # 基金使用净值走势
        try:
            nav = get_etf_nav_history(symbol, days=days if days > 0 else 180)
            if not nav:
                return {"symbol": symbol, "market": market, "klines": [], "count": 0}

            points = nav[-days:] if days > 0 else nav
            klines = [
                {
                    "date": p.get("date"),
                    "close": p.get("unit_nav"),
                    "cum_nav": p.get("cum_nav"),
                    "return_pct": p.get("change_pct"),
                }
                for p in points
            ]
            return {
                "symbol": symbol,
                "market": market,
                "klines": klines,
                "count": len(klines),
            }
        except Exception as e:
            raise McpToolError(
                error_code=ERR_INTERNAL,
                message=f"获取基金净值失败: {e}",
                hint="请检查基金代码是否正确",
                retryable=True,
            )

    try:
        market_code = MarketCode(market)
    except ValueError:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message=f"不支持的市场: {market}",
            hint="市场类型仅支持 CN/HK/US/FUND",
        )

    try:
        collector = KlineCollector(market_code)
        # Note: KlineCollector currently only supports daily klines
        klines = collector.get_klines(symbol, days=days)
        items = [
            {
                "date": k.date,
                "open": k.open,
                "close": k.close,
                "high": k.high,
                "low": k.low,
                "volume": k.volume,
            }
            for k in klines
        ]
        return {
            "symbol": symbol,
            "market": market,
            "interval": interval,
            "klines": items,
            "count": len(items),
        }
    except Exception as e:
        raise McpToolError(
            error_code=ERR_INTERNAL,
            message=f"获取K线失败: {e}",
            hint="请检查代码和市场是否正确",
            retryable=True,
        )


def _get_kline_summary(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = db
    _require_args(arguments, ["symbol", "market"])
    symbol = str(arguments["symbol"]).strip()
    market = str(arguments["market"]).strip().upper()

    if market == "FUND":
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message="基金暂不支持技术指标摘要",
            hint="请使用 klines.get 获取基金净值走势",
        )

    try:
        market_code = MarketCode(market)
    except ValueError:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message=f"不支持的市场: {market}",
            hint="市场类型仅支持 CN/HK/US",
        )

    try:
        collector = KlineCollector(market_code)
        summary = collector.get_kline_summary(symbol)
        return {
            "symbol": symbol,
            "market": market,
            **summary,
        }
    except Exception as e:
        raise McpToolError(
            error_code=ERR_INTERNAL,
            message=f"获取技术指标失败: {e}",
            hint="请检查代码和市场是否正确",
            retryable=True,
        )


# ==================== 分析历史 ====================

def _format_datetime(dt) -> str:
    if not dt:
        return ""
    tz_name = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo).isoformat(timespec="seconds")


def _list_history(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    agent_name = arguments.get("agent_name")
    stock_symbol = arguments.get("stock_symbol")
    kind = str(arguments.get("kind", AGENT_KIND_WORKFLOW))
    limit = int(arguments.get("limit", 30))

    query = db.query(AnalysisHistory)

    if agent_name:
        query = query.filter(AnalysisHistory.agent_name == agent_name)
    if stock_symbol:
        query = query.filter(AnalysisHistory.stock_symbol == stock_symbol)

    kind_norm = (kind or "").strip().lower()
    if kind_norm == AGENT_KIND_CAPABILITY:
        query = query.filter(
            or_(
                AnalysisHistory.agent_kind_snapshot == AGENT_KIND_CAPABILITY,
                and_(
                    or_(
                        AnalysisHistory.agent_kind_snapshot.is_(None),
                        AnalysisHistory.agent_kind_snapshot == "",
                    ),
                    AnalysisHistory.agent_name.in_(CAPABILITY_AGENT_NAMES),
                ),
            )
        )
    elif kind_norm == AGENT_KIND_WORKFLOW:
        query = query.filter(
            or_(
                AnalysisHistory.agent_kind_snapshot == AGENT_KIND_WORKFLOW,
                and_(
                    or_(
                        AnalysisHistory.agent_kind_snapshot.is_(None),
                        AnalysisHistory.agent_kind_snapshot == "",
                    ),
                    ~AnalysisHistory.agent_name.in_(CAPABILITY_AGENT_NAMES),
                ),
            )
        )

    records = (
        query.order_by(
            AnalysisHistory.analysis_date.desc(),
            AnalysisHistory.updated_at.desc(),
            AnalysisHistory.id.desc(),
        )
        .limit(min(limit, 100))
        .all()
    )

    items = [
        {
            "id": r.id,
            "agent_name": r.agent_name,
            "agent_kind": r.agent_kind_snapshot or infer_agent_kind(r.agent_name),
            "stock_symbol": r.stock_symbol,
            "analysis_date": r.analysis_date,
            "title": r.title,
            "created_at": _format_datetime(r.created_at),
            "updated_at": _format_datetime(r.updated_at),
        }
        for r in records
    ]
    return {"items": items, "count": len(items)}


def _get_history(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["history_id"])
    history_id = int(arguments["history_id"])

    record = db.query(AnalysisHistory).filter(
        AnalysisHistory.id == history_id).first()
    if not record:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="分析历史不存在",
            hint="请检查 history_id 是否正确",
        )

    return {
        "id": record.id,
        "agent_name": record.agent_name,
        "agent_kind": record.agent_kind_snapshot or infer_agent_kind(record.agent_name),
        "stock_symbol": record.stock_symbol,
        "analysis_date": record.analysis_date,
        "title": record.title,
        "content": record.content,
        "suggestions": record.suggestions,
        "quality_overview": record.quality_overview,
        "context_summary": record.context_summary,
        "created_at": _format_datetime(record.created_at),
        "updated_at": _format_datetime(record.updated_at),
    }


# ==================== 建议池 ====================

def _get_latest_suggestions(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = db
    symbols = arguments.get("symbols")
    include_expired = bool(arguments.get("include_expired", False))

    symbol_list = None
    if symbols:
        if isinstance(symbols, str):
            symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
        elif isinstance(symbols, list):
            symbol_list = [str(s).strip() for s in symbols if str(s).strip()]

    suggestions = get_latest_suggestions(
        stock_symbols=symbol_list,
        include_expired=include_expired,
    )
    return {"items": suggestions, "count": len(suggestions)}


def _get_stock_suggestions(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = db
    _require_args(arguments, ["symbol"])
    symbol = str(arguments["symbol"]).strip()
    market = str(arguments.get("market", "")).strip().upper() or None
    include_expired = bool(arguments.get("include_expired", False))
    limit = int(arguments.get("limit", 10))

    suggestions = get_suggestions_for_stock(
        stock_symbol=symbol,
        stock_market=market,
        include_expired=include_expired,
        limit=limit,
    )
    return {"items": suggestions, "count": len(suggestions)}


# ==================== Agent 操作 ====================

def _list_agents(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    kind = str(arguments.get("kind", "")).strip().lower()
    include_internal = bool(arguments.get("include_internal", False))

    query = db.query(AgentConfig)
    if not include_internal:
        query = query.filter(AgentConfig.visible == True)
    if kind == AGENT_KIND_WORKFLOW:
        query = query.filter(AgentConfig.kind == AGENT_KIND_WORKFLOW)
    elif kind == AGENT_KIND_CAPABILITY:
        query = query.filter(AgentConfig.kind == AGENT_KIND_CAPABILITY)

    agents = query.order_by(
        AgentConfig.display_order.asc(), AgentConfig.name.asc()).all()

    items = [
        {
            "name": a.name,
            "display_name": a.display_name,
            "description": a.description,
            "kind": a.kind or infer_agent_kind(a.name),
            "enabled": a.enabled,
            "schedule": a.schedule or "",
            "execution_mode": a.execution_mode or "batch",
            "visible": bool(a.visible),
        }
        for a in agents
    ]
    return {"items": items, "count": len(items)}


def _agents_health(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    include_internal = bool(arguments.get("include_internal", False))
    tz = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz)
    except Exception:
        tzinfo = timezone.utc

    now = datetime.now(tzinfo)
    horizon = now + timedelta(hours=24)

    query = db.query(AgentConfig)
    if not include_internal:
        query = query.filter(
            AgentConfig.kind == AGENT_KIND_WORKFLOW,
            AgentConfig.visible == True,
        )
    agents = query.order_by(
        AgentConfig.display_order.asc(), AgentConfig.name.asc()).all()

    out = []
    next_24h_count = 0
    recent_failed_count = 0

    for a in agents:
        next_runs: list[str] = []
        if a.enabled and (a.schedule or "").strip():
            try:
                runs = preview_schedule(a.schedule, count=3, timezone=tz)
                next_runs = [r.isoformat() for r in runs]
                next_24h_count += count_runs_within(
                    a.schedule, start=now, end=horizon, timezone=tz
                )
            except Exception:
                next_runs = []

        last = (
            db.query(AgentRun)
            .filter(AgentRun.agent_name == a.name)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .first()
        )
        last_run = None
        if last:
            last_run = {
                "status": last.status or "",
                "created_at": _format_datetime(last.created_at),
                "duration_ms": last.duration_ms or 0,
                "error": last.error or "",
            }
            if a.enabled and (last.status or "") == "failed":
                recent_failed_count += 1

        out.append({
            "name": a.name,
            "display_name": a.display_name,
            "kind": a.kind or infer_agent_kind(a.name),
            "enabled": a.enabled,
            "schedule": a.schedule or "",
            "next_runs": next_runs,
            "last_run": last_run,
        })

    return {
        "timezone": tz,
        "summary": {
            "next_24h_count": next_24h_count,
            "recent_failed_count": recent_failed_count,
        },
        "agents": out,
    }


def _trigger_agent(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["agent_name"])
    agent_name = str(arguments["agent_name"]).strip()
    symbol = arguments.get("symbol")
    market = arguments.get("market")

    agent = db.query(AgentConfig).filter(
        AgentConfig.name == agent_name).first()
    if not agent:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message=f"Agent {agent_name} 不存在",
            hint="请使用 agents.list 查询可用 Agent",
        )

    # 异步触发（不等待结果）
    try:
        from server import AGENT_REGISTRY, run_agent_for_symbol
        if agent_name not in AGENT_REGISTRY:
            raise McpToolError(
                error_code=ERR_NOT_FOUND,
                message=f"Agent {agent_name} 未注册",
                hint="该 Agent 可能尚未加载或不可触发",
            )

        import threading

        def _run():
            try:
                if symbol and market:
                    asyncio.run(run_agent_for_symbol(
                        agent_name, symbol, market))
                else:
                    from server import run_agent
                    asyncio.run(run_agent(agent_name))
            except Exception as e:
                logger.error(f"触发 Agent {agent_name} 失败: {e}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        return {
            "triggered": True,
            "agent_name": agent_name,
            "symbol": symbol,
            "market": market,
            "message": "Agent 已触发，请稍后查看执行结果",
        }
    except McpToolError:
        raise
    except Exception as e:
        raise McpToolError(
            error_code=ERR_INTERNAL,
            message=f"触发 Agent 失败: {e}",
            hint="请检查服务日志",
            retryable=True,
        )


# ==================== 价格提醒 ====================

def _alert_rule_to_dict(rule: PriceAlertRule) -> dict[str, Any]:
    stock = rule.stock
    return {
        "id": rule.id,
        "stock_id": rule.stock_id,
        "stock_symbol": stock.symbol if stock else "",
        "stock_name": stock.name if stock else "",
        "market": stock.market if stock else "",
        "name": rule.name,
        "enabled": rule.enabled,
        "condition_group": rule.condition_group or {},
        "market_hours_mode": rule.market_hours_mode,
        "cooldown_minutes": rule.cooldown_minutes,
        "max_triggers_per_day": rule.max_triggers_per_day,
        "repeat_mode": rule.repeat_mode,
        "expire_at": rule.expire_at.isoformat() if rule.expire_at else None,
        "notify_channel_ids": rule.notify_channel_ids or [],
        "last_trigger_at": rule.last_trigger_at.isoformat() if rule.last_trigger_at else None,
        "trigger_count_today": rule.trigger_count_today or 0,
    }


def _list_price_alerts(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    stock_id = arguments.get("stock_id")
    enabled_only = bool(arguments.get("enabled_only", False))

    query = db.query(PriceAlertRule).join(Stock)
    if stock_id:
        query = query.filter(PriceAlertRule.stock_id == int(stock_id))
    if enabled_only:
        query = query.filter(PriceAlertRule.enabled == True)

    rows = query.order_by(PriceAlertRule.updated_at.desc(),
                          PriceAlertRule.id.desc()).all()
    items = [_alert_rule_to_dict(r) for r in rows]
    return {"items": items, "count": len(items)}


def _validate_condition_group(group: dict) -> None:
    op = group.get("op", "and")
    items = group.get("items", [])

    if op not in ("and", "or"):
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message="condition_group.op 仅支持 and/or",
            hint="使用 and 表示全部满足，or 表示任一满足",
        )
    if not items:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message="condition_group.items 不能为空",
            hint="至少需要一个条件项",
        )

    allowed_types = {"price", "change_pct",
                     "turnover", "volume", "volume_ratio"}
    allowed_ops = {">=", "<=", ">", "<", "==",
                   "=", "!=", "<>", "between", "in"}
    for it in items:
        if it.get("type") not in allowed_types:
            raise McpToolError(
                error_code=ERR_INVALID_PARAMS,
                message=f"不支持的条件类型: {it.get('type')}",
                hint=f"支持的类型: {', '.join(allowed_types)}",
            )
        if it.get("op") not in allowed_ops:
            raise McpToolError(
                error_code=ERR_INVALID_PARAMS,
                message=f"不支持的运算符: {it.get('op')}",
                hint=f"支持的运算符: {', '.join(allowed_ops)}",
            )


def _create_price_alert(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["stock_id", "condition_group"])
    stock_id = int(arguments["stock_id"])
    condition_group = arguments["condition_group"]

    stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not stock:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="股票不存在",
            hint="请先添加股票到自选",
        )

    _validate_condition_group(condition_group)

    expire_at = None
    if arguments.get("expire_at"):
        try:
            expire_at = datetime.fromisoformat(arguments["expire_at"])
        except Exception:
            raise McpToolError(
                error_code=ERR_INVALID_PARAMS,
                message="expire_at 格式错误",
                hint="请使用 ISO 格式：YYYY-MM-DDTHH:MM:SS",
            )

    rule = PriceAlertRule(
        stock_id=stock_id,
        name=str(arguments.get("name", "")).strip() or f"{stock.name} 提醒",
        enabled=bool(arguments.get("enabled", True)),
        condition_group=condition_group,
        market_hours_mode=arguments.get("market_hours_mode", "trading_only"),
        cooldown_minutes=max(0, int(arguments.get("cooldown_minutes", 30))),
        max_triggers_per_day=max(
            0, int(arguments.get("max_triggers_per_day", 3))),
        repeat_mode=arguments.get("repeat_mode", "repeat"),
        expire_at=expire_at,
        notify_channel_ids=arguments.get("notify_channel_ids", []),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _alert_rule_to_dict(rule)


def _update_price_alert(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["rule_id"])
    rule_id = int(arguments["rule_id"])

    rule = db.query(PriceAlertRule).filter(
        PriceAlertRule.id == rule_id).first()
    if not rule:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="规则不存在",
            hint="请检查 rule_id 是否正确",
        )

    if "name" in arguments:
        rule.name = str(arguments["name"]).strip()
    if "enabled" in arguments:
        rule.enabled = bool(arguments["enabled"])
    if "condition_group" in arguments:
        _validate_condition_group(arguments["condition_group"])
        rule.condition_group = arguments["condition_group"]
    if "market_hours_mode" in arguments:
        rule.market_hours_mode = arguments["market_hours_mode"]
    if "cooldown_minutes" in arguments:
        rule.cooldown_minutes = max(0, int(arguments["cooldown_minutes"]))
    if "max_triggers_per_day" in arguments:
        rule.max_triggers_per_day = max(
            0, int(arguments["max_triggers_per_day"]))
    if "repeat_mode" in arguments:
        rule.repeat_mode = arguments["repeat_mode"]
    if "expire_at" in arguments:
        val = arguments["expire_at"]
        if val:
            try:
                rule.expire_at = datetime.fromisoformat(val)
            except Exception:
                raise McpToolError(
                    error_code=ERR_INVALID_PARAMS,
                    message="expire_at 格式错误",
                    hint="请使用 ISO 格式",
                )
        else:
            rule.expire_at = None
    if "notify_channel_ids" in arguments:
        rule.notify_channel_ids = arguments["notify_channel_ids"]

    db.commit()
    db.refresh(rule)
    return _alert_rule_to_dict(rule)


def _delete_price_alert(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["rule_id"])
    rule_id = int(arguments["rule_id"])

    rule = db.query(PriceAlertRule).filter(
        PriceAlertRule.id == rule_id).first()
    if not rule:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="规则不存在",
            hint="请检查 rule_id 是否正确",
        )

    db.delete(rule)
    db.commit()
    return {"success": True, "rule_id": rule_id}


def _toggle_price_alert(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _require_args(arguments, ["rule_id", "enabled"])
    rule_id = int(arguments["rule_id"])
    enabled = bool(arguments["enabled"])

    rule = db.query(PriceAlertRule).filter(
        PriceAlertRule.id == rule_id).first()
    if not rule:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="规则不存在",
            hint="请检查 rule_id 是否正确",
        )

    rule.enabled = enabled
    db.commit()
    db.refresh(rule)
    return _alert_rule_to_dict(rule)


def _scan_price_alerts(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = db
    dry_run = bool(arguments.get("dry_run", False))
    bypass_market_hours = bool(arguments.get("bypass_market_hours", True))

    import threading

    result_holder = {"result": None, "error": None}

    def _run():
        try:
            result_holder["result"] = asyncio.run(
                ENGINE.scan_once(
                    dry_run=dry_run, bypass_market_hours=bypass_market_hours)
            )
        except Exception as e:
            result_holder["error"] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=30)  # Wait up to 30 seconds

    if result_holder["error"]:
        raise McpToolError(
            error_code=ERR_INTERNAL,
            message=f"扫描失败: {result_holder['error']}",
            hint="请检查服务日志",
            retryable=True,
        )

    return result_holder["result"] or {"scanned": True, "triggered_count": 0, "dry_run": dry_run}


# ==================== 基金专用 ====================

def _fund_overview(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = db
    _require_args(arguments, ["fund_code"])
    fund_code = str(arguments["fund_code"]).strip()

    try:
        overview = get_etf_overview(fund_code, top=10, nav_days=180)

        return {
            "fund_code": fund_code,
            "spot": overview.get("spot"),
            "top_holdings": overview.get("holdings") or [],
            "nav_history": overview.get("nav_history") or [],
            "updated_at": int(time.time() * 1000),
        }
    except Exception as e:
        raise McpToolError(
            error_code=ERR_INTERNAL,
            message=f"获取基金详情失败: {e}",
            hint="请检查基金代码是否正确",
            retryable=True,
        )


def _fund_holdings(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = db
    _require_args(arguments, ["fund_code"])
    fund_code = str(arguments["fund_code"]).strip()
    topline = int(arguments.get("topline", 10))

    try:
        holdings = get_etf_holdings(fund_code, top=topline)
        return {
            "fund_code": fund_code,
            "holdings": holdings,
            "count": len(holdings),
        }
    except Exception as e:
        raise McpToolError(
            error_code=ERR_INTERNAL,
            message=f"获取基金重仓失败: {e}",
            hint="请检查基金代码是否正确",
            retryable=True,
        )


# ==================== 工具类 ====================

def _get_exchange_rates(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = arguments
    _ = db
    return {
        "HKD_CNY": get_hkd_cny_rate(),
        "USD_CNY": get_usd_cny_rate(),
        "updated_at": int(time.time() * 1000),
    }


def _get_quote(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = db
    _require_args(arguments, ["symbol", "market"])
    symbol = str(arguments["symbol"]).strip()
    market = str(arguments["market"]).strip().upper()

    if market == "FUND":
        item = get_etf_spot(symbol)
        if not item:
            raise McpToolError(
                error_code=ERR_NOT_FOUND,
                message="基金行情不存在",
                hint="请检查基金代码是否正确",
            )
        return {
            "symbol": symbol,
            "market": market,
            "name": item.get("name"),
            "current_price": item.get("price"),
            "iopv": item.get("iopv"),
            "premium_pct": item.get("premium_pct"),
            "change_pct": item.get("change_pct"),
            "turnover": item.get("turnover"),
            "total_value": item.get("total_value"),
            "volume": item.get("volume"),
        }

    try:
        market_code = MarketCode(market)
    except ValueError:
        raise McpToolError(
            error_code=ERR_INVALID_PARAMS,
            message=f"不支持的市场: {market}",
            hint="市场类型仅支持 CN/HK/US/FUND",
        )

    tencent_symbol = _tencent_symbol(symbol, market_code)
    items = _fetch_tencent_quotes([tencent_symbol])
    if not items:
        raise McpToolError(
            error_code=ERR_NOT_FOUND,
            message="行情不存在",
            hint="请检查代码和市场是否正确",
        )

    item = items[0]
    return {
        "symbol": symbol,
        "market": market,
        "name": item.get("name"),
        "current_price": item.get("current_price"),
        "prev_close": item.get("prev_close"),
        "open_price": item.get("open_price"),
        "high_price": item.get("high_price"),
        "low_price": item.get("low_price"),
        "volume": item.get("volume"),
        "change_pct": item.get("change_pct"),
        "change_amount": item.get("change_amount"),
        "pe_ratio": item.get("pe_ratio"),
        "total_market_value": item.get("total_market_value"),
        "circulating_market_value": item.get("circulating_market_value"),
    }


def _get_quotes_batch(arguments: dict[str, Any], db: Session) -> dict[str, Any]:
    _ = db
    items = arguments.get("items")
    if not items:
        return {"quotes": [], "count": 0}

    # 按市场分组
    market_symbols: dict[str, list[str]] = {}
    for item in items:
        market = str(item.get("market", "CN")).strip().upper()
        symbol = str(item.get("symbol", "")).strip()
        if symbol:
            market_symbols.setdefault(market, []).append(symbol)

    results = []
    for market, symbols in market_symbols.items():
        if market == "FUND":
            try:
                for sym in symbols:
                    spot = get_etf_spot(sym)
                    if not spot:
                        continue
                    results.append({
                        "symbol": spot.get("symbol") or sym,
                        "market": market,
                        "name": spot.get("name"),
                        "current_price": spot.get("price"),
                        "iopv": spot.get("iopv"),
                        "change_pct": spot.get("change_pct"),
                    })
            except Exception:
                pass
            continue

        try:
            market_code = MarketCode(market)
            tencent_symbols = [_tencent_symbol(
                s, market_code) for s in symbols]
            quote_items = _fetch_tencent_quotes(tencent_symbols)
            for qi in quote_items:
                results.append({
                    "symbol": qi.get("symbol"),
                    "market": market,
                    "name": qi.get("name"),
                    "current_price": qi.get("current_price"),
                    "prev_close": qi.get("prev_close"),
                    "change_pct": qi.get("change_pct"),
                    "change_amount": qi.get("change_amount"),
                })
        except Exception:
            pass

    return {"quotes": results, "count": len(results)}


# 需要导入 timedelta


TOOLS: list[dict[str, Any]] = [
    {
        "name": "positions.list",
        "description": "查询持仓列表，支持按账户或股票筛选",
        "access": "read",
        "tags": ["positions", "portfolio", "read", "pagination"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "按条件查询持仓。若不传任何参数，返回全部持仓。",
            "properties": {
                "account_id": {
                    "type": "integer",
                    "description": "账户 ID。传入后仅返回该账户的持仓。",
                },
                "stock_id": {
                    "type": "integer",
                    "description": "股票 ID。传入后仅返回该股票的持仓。",
                },
                "page": {
                    "type": "integer",
                    "default": 1,
                    "description": "页码，从 1 开始。",
                },
                "page_size": {
                    "type": "integer",
                    "default": 50,
                    "description": "每页条数，范围 1-200。",
                },
                "since_id": {
                    "type": "integer",
                    "default": 0,
                    "description": "增量游标：仅返回 id > since_id 的记录。",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "持仓查询结果。",
            "properties": {
                "items": {"type": "array", "description": "持仓列表。"},
                "count": {"type": "integer", "description": "返回条数。"},
                "total": {"type": "integer", "description": "总条数。"},
                "page": {"type": "integer", "description": "当前页码。"},
                "page_size": {"type": "integer", "description": "当前页大小。"},
                "has_more": {"type": "boolean", "description": "是否还有下一页。"},
                "next_since_id": {"type": "integer", "description": "下一次增量请求可用的游标。"},
            },
        },
        "examples": [
            {"title": "查询全部持仓", "arguments": {}},
            {"title": "查询单账户持仓", "arguments": {"account_id": 1}},
        ],
    },
    {
        "name": "positions.create",
        "description": "新增持仓",
        "access": "write",
        "tags": ["positions", "portfolio", "write"],
        "risk_level": "high",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "创建一条新持仓记录。一个账户同一股票仅允许一条持仓。",
            "required": ["account_id", "stock_id", "cost_price", "quantity"],
            "properties": {
                "account_id": {
                    "type": "integer",
                    "description": "账户 ID。",
                },
                "stock_id": {
                    "type": "integer",
                    "description": "股票 ID。",
                },
                "cost_price": {
                    "type": "number",
                    "description": "持仓成本价（按股票原币种）。",
                },
                "quantity": {
                    "type": "integer",
                    "description": "持仓数量（股/份，整数）。",
                },
                "invested_amount": {
                    "type": ["number", "null"],
                    "description": "投入金额（可选，用于监控计算，按原币种）。",
                },
                "trading_style": {
                    "type": ["string", "null"],
                    "enum": ["short", "swing", "long", ""],
                    "description": "交易风格：short=短线，swing=波段，long=长线。",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "新创建的持仓对象。",
            "properties": {
                "id": {"type": "integer", "description": "持仓记录 ID。"},
                "account_id": {"type": "integer", "description": "账户 ID。"},
                "stock_id": {"type": "integer", "description": "股票 ID。"},
            },
        },
        "examples": [
            {
                "title": "新增持仓",
                "arguments": {"account_id": 1, "stock_id": 1, "cost_price": 12.3, "quantity": 100},
            }
        ],
    },
    {
        "name": "positions.update",
        "description": "修改持仓",
        "access": "write",
        "tags": ["positions", "portfolio", "write"],
        "risk_level": "high",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "按 position_id 更新持仓字段。只更新传入的字段。",
            "required": ["position_id"],
            "properties": {
                "position_id": {
                    "type": "integer",
                    "description": "持仓记录 ID。",
                },
                "cost_price": {
                    "type": "number",
                    "description": "新的持仓成本价。",
                },
                "quantity": {
                    "type": "integer",
                    "description": "新的持仓数量（整数）。",
                },
                "invested_amount": {
                    "type": ["number", "null"],
                    "description": "新的投入金额；传 null 可清空。",
                },
                "trading_style": {
                    "type": ["string", "null"],
                    "enum": ["short", "swing", "long", ""],
                    "description": "交易风格；传空字符串或 null 可清空。",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "更新后的持仓对象。",
            "properties": {
                "id": {"type": "integer", "description": "持仓记录 ID。"},
                "quantity": {"type": "number", "description": "更新后的持仓数量。"},
                "cost_price": {"type": "number", "description": "更新后的成本价。"},
            },
        },
        "examples": [
            {"title": "更新持仓数量", "arguments": {"position_id": 10, "quantity": 200}},
        ],
    },
    {
        "name": "positions.trade",
        "description": "对现有持仓执行加仓/减仓，并写入交易记录",
        "access": "write",
        "tags": ["positions", "portfolio", "write"],
        "risk_level": "high",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "对 position_id 执行 add（加仓）/reduce（减仓），并落库记录。",
            "required": ["position_id", "action", "quantity", "price"],
            "properties": {
                "position_id": {"type": "integer", "description": "持仓记录 ID。"},
                "action": {"type": "string", "enum": ["add", "reduce"], "description": "交易动作：add=加仓，reduce=减仓。"},
                "quantity": {"type": "integer", "description": "本次交易数量（股/份，整数）。"},
                "price": {"type": "number", "description": "本次交易价格。"},
                "trade_date": {"type": ["string", "null"], "description": "交易日期 YYYY-MM-DD（可选，不传用当前时间）。"},
                "note": {"type": ["string", "null"], "description": "备注（可选）。"}
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "新写入的交易记录。",
            "properties": {
                "id": {"type": "integer"},
                "position_id": {"type": "integer"},
                "side": {"type": "string"},
                "quantity": {"type": "integer"},
                "price": {"type": "number"},
                "amount": {"type": "number"},
                "cost_before": {"type": "number"},
                "qty_before": {"type": "integer"},
                "cost_after": {"type": "number"},
                "qty_after": {"type": "integer"}
            },
        },
        "examples": [
            {"title": "加仓", "arguments": {"position_id": 10,
                                          "action": "add", "quantity": 20, "price": 11.5}},
            {"title": "减仓", "arguments": {"position_id": 10,
                                          "action": "reduce", "quantity": 10, "price": 12.0}},
        ],
    },
    {
        "name": "positions.trades.list",
        "description": "查询持仓交易记录（倒序）",
        "access": "read",
        "tags": ["positions", "portfolio", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "required": ["position_id"],
            "properties": {
                "position_id": {"type": "integer", "description": "持仓记录 ID。"},
                "page": {"type": "integer", "default": 1, "description": "页码，从1开始。"},
                "page_size": {"type": "integer", "default": 5, "description": "每页条数 1-200。"}
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "description": "交易记录列表。"},
                "count": {"type": "integer", "description": "返回条数。"},
                "total": {"type": "integer", "description": "总条数。"},
                "page": {"type": "integer", "description": "当前页码。"},
                "page_size": {"type": "integer", "description": "每页条数。"},
                "has_more": {"type": "boolean", "description": "是否有下一页。"}
            },
        },
        "examples": [
            {"title": "查看交易记录", "arguments": {"position_id": 10}},
            {"title": "查看第2页", "arguments": {
                "position_id": 10, "page": 2, "page_size": 5}},
        ],
    },
    {
        "name": "positions.delete",
        "description": "删除持仓",
        "access": "write",
        "tags": ["positions", "portfolio", "write", "dangerous"],
        "risk_level": "high",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "按 position_id 删除持仓记录。",
            "required": ["position_id"],
            "properties": {
                "position_id": {
                    "type": "integer",
                    "description": "待删除的持仓记录 ID。",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "删除结果。",
            "properties": {
                "success": {"type": "boolean", "description": "是否删除成功。"},
                "position_id": {"type": "integer", "description": "被删除的持仓 ID。"},
            },
        },
        "examples": [
            {"title": "删除持仓", "arguments": {"position_id": 10}},
        ],
    },
    {
        "name": "positions.reorder.batch",
        "description": "批量更新持仓排序",
        "access": "write",
        "tags": ["positions", "write", "batch"],
        "risk_level": "medium",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "批量更新 sort_order，用于前端拖拽排序后的持久化。",
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "description": "排序项列表。",
                    "items": {
                        "type": "object",
                        "required": ["id", "sort_order"],
                        "properties": {
                            "id": {
                                "type": "integer",
                                "description": "持仓记录 ID。",
                            },
                            "sort_order": {
                                "type": "integer",
                                "description": "排序值，越小越靠前。",
                            },
                        },
                        "additionalProperties": False,
                    },
                }
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "批量排序更新结果。",
            "properties": {
                "updated": {"type": "integer", "description": "实际更新条数。"},
            },
        },
        "examples": [
            {
                "title": "批量重排",
                "arguments": {"items": [{"id": 1, "sort_order": 1}, {"id": 2, "sort_order": 2}]},
            }
        ],
    },
    {
        "name": "portfolio.summary",
        "description": "查询持仓汇总（默认不拉取实时行情）",
        "access": "read",
        "tags": ["portfolio", "read", "summary"],
        "risk_level": "low",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "返回账户维度和总体维度的持仓汇总统计。",
            "properties": {
                "account_id": {
                    "type": "integer",
                    "description": "账户 ID。传入后仅汇总该账户。",
                },
                "include_quotes": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否拉取实时行情后再计算浮盈亏。",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "组合汇总结果。",
            "properties": {
                "accounts": {"type": "array", "description": "账户维度汇总列表。"},
                "total": {"type": "object", "description": "总计汇总。"},
                "exchange_rates": {"type": "object", "description": "使用到的汇率。"},
            },
        },
        "examples": [
            {"title": "汇总全部账户", "arguments": {"include_quotes": False}},
            {"title": "汇总单账户", "arguments": {
                "account_id": 1, "include_quotes": True}},
        ],
    },
    {
        "name": "stocks.list",
        "description": "查询自选股列表",
        "access": "read",
        "tags": ["stocks", "watchlist", "read", "pagination"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "返回自选股基础信息（代码、名称、市场、排序）。",
            "properties": {
                "page": {
                    "type": "integer",
                    "default": 1,
                    "description": "页码，从 1 开始。",
                },
                "page_size": {
                    "type": "integer",
                    "default": 50,
                    "description": "每页条数，范围 1-200。",
                },
                "since_id": {
                    "type": "integer",
                    "default": 0,
                    "description": "增量游标：仅返回 id > since_id 的记录。",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "自选股列表结果。",
            "properties": {
                "items": {"type": "array", "description": "自选股列表。"},
                "count": {"type": "integer", "description": "返回条数。"},
                "total": {"type": "integer", "description": "总条数。"},
                "page": {"type": "integer", "description": "当前页码。"},
                "page_size": {"type": "integer", "description": "当前页大小。"},
                "has_more": {"type": "boolean", "description": "是否还有下一页。"},
                "next_since_id": {"type": "integer", "description": "下一次增量请求可用的游标。"},
            },
        },
        "examples": [{"title": "查询自选股", "arguments": {}}],
    },
    {
        "name": "stocks.quotes",
        "description": "查询自选股实时行情",
        "access": "read",
        "tags": ["stocks", "watchlist", "quotes", "read"],
        "risk_level": "low",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "返回当前自选股的实时行情快照。",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "自选股行情快照。",
            "properties": {
                "quotes": {"type": "object", "description": "key 为 symbol 的行情字典。"},
            },
        },
        "examples": [{"title": "查询自选行情", "arguments": {}}],
    },
    {
        "name": "market.indices",
        "description": "查询主要市场指数（上证/深证/创业板/恒指/纳指/道指）",
        "access": "read",
        "tags": ["market", "indices", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "返回主要市场指数行情。",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "市场指数行情结果。",
            "properties": {
                "items": {"type": "array", "description": "指数列表。"},
            },
        },
        "examples": [{"title": "查询指数", "arguments": {}}],
    },
    {
        "name": "dashboard.overview",
        "description": "查询 Dashboard 聚合数据（KPI、机会、风险、大盘热度等）",
        "access": "read",
        "tags": ["dashboard", "summary", "read"],
        "risk_level": "low",
        "cost_hint": "high",
        "inputSchema": {
            "type": "object",
            "description": "返回首页聚合数据，适用于驾驶舱总览。",
            "properties": {
                "market": {
                    "type": "string",
                    "enum": ["ALL", "CN", "HK", "US"],
                    "default": "ALL",
                    "description": "市场过滤：ALL/CN/HK/US。",
                },
                "action_limit": {
                    "type": "integer",
                    "default": 6,
                    "description": "行动机会列表返回条数。",
                },
                "risk_limit": {
                    "type": "integer",
                    "default": 6,
                    "description": "风险列表返回条数。",
                },
                "days": {
                    "type": "integer",
                    "default": 45,
                    "description": "策略统计回看天数。",
                },
                "lite": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否返回简版字段集合（用于低带宽快速展示）。",
                },
                "fields": {
                    "type": ["string", "array"],
                    "description": "字段过滤：逗号分隔字符串或字段数组，例如 'kpis,portfolio'。",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "首页聚合结果。",
            "properties": {
                "kpis": {"type": "object", "description": "关键指标汇总。"},
                "portfolio": {"type": "object", "description": "组合统计。"},
                "action_center": {"type": "object", "description": "行动建议与风险项。"},
                "market_pulse": {"type": "object", "description": "市场热度。"},
            },
        },
        "examples": [
            {"title": "默认概览", "arguments": {}},
            {"title": "A股概览", "arguments": {"market": "CN",
                                            "action_limit": 8, "risk_limit": 8, "days": 30}},
            {"title": "简版概览", "arguments": {"lite": True}},
            {"title": "字段过滤", "arguments": {"fields": ["kpis", "portfolio"]}},
        ],
    },
    {
        "name": "mcp.health",
        "description": "MCP 健康检查",
        "access": "read",
        "tags": ["mcp", "diagnostic", "health"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "返回 MCP 服务运行状态。",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "服务状态，正常为 ok。"},
                "version": {"type": "string", "description": "MCP 服务版本。"},
                "time_ms": {"type": "integer", "description": "服务时间戳（毫秒）。"},
            },
        },
        "examples": [{"title": "健康检查", "arguments": {}}],
    },
    {
        "name": "mcp.auth.status",
        "description": "当前 MCP 鉴权状态",
        "access": "read",
        "tags": ["mcp", "diagnostic", "auth"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "返回当前连接用户和鉴权方式。",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "当前用户标识。"},
                "auth": {"type": "string", "description": "鉴权方式：bearer/basic。"},
            },
        },
        "examples": [{"title": "查询鉴权状态", "arguments": {}}],
    },
    {
        "name": "mcp.version",
        "description": "MCP 版本信息",
        "access": "read",
        "tags": ["mcp", "diagnostic", "version"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "返回 MCP 协议版本与服务版本。",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "服务名称。"},
                "version": {"type": "string", "description": "服务版本号。"},
                "protocolVersion": {"type": "string", "description": "MCP 协议版本。"},
            },
        },
        "examples": [{"title": "查询版本", "arguments": {}}],
    },
    {
        "name": "mcp.logs.query",
        "description": "查询 MCP 审计操作日志",
        "access": "read",
        "tags": ["mcp", "audit", "logs", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "查询 MCP 写操作审计日志，支持按工具、状态、用户筛选。",
            "properties": {
                "tool_name": {"type": "string", "description": "按工具名过滤，例如 positions.create。"},
                "status": {"type": "string", "description": "按状态过滤，例如 success / error:MCP_RESOURCE_CONFLICT。"},
                "user": {"type": "string", "description": "按 MCP 用户名过滤。"},
                "auth": {"type": "string", "enum": ["", "basic", "bearer"], "default": "", "description": "按鉴权方式过滤。"},
                "level": {"type": "string", "description": "日志级别过滤，逗号分隔。"},
                "q": {"type": "string", "description": "关键词搜索。"},
                "limit": {"type": "integer", "default": 50, "description": "返回条数 1-200。"},
                "before_id": {"type": "integer", "default": 0, "description": "cursor 分页：取该 id 之前日志。"}
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "description": "审计日志项。"},
                "count": {"type": "integer", "description": "本次返回条数。"},
                "has_more": {"type": "boolean", "description": "是否还有更多。"},
                "next_before_id": {"type": ["integer", "null"], "description": "下一页 cursor。"},
            },
        },
        "examples": [
            {"title": "查看最近审计日志", "arguments": {"limit": 20}},
            {"title": "筛选持仓写操作", "arguments": {
                "tool_name": "positions.create", "limit": 20}},
            {"title": "只看失败操作", "arguments": {
                "status": "error:MCP_RESOURCE_CONFLICT", "limit": 20}},
        ],
    },
    # ==================== 自选股管理 ====================
    {
        "name": "stocks.create",
        "description": "添加自选股",
        "access": "write",
        "tags": ["stocks", "watchlist", "write"],
        "risk_level": "medium",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "创建新的自选股。",
            "required": ["symbol", "name"],
            "properties": {
                "symbol": {"type": "string", "description": "股票代码。"},
                "name": {"type": "string", "description": "股票名称。"},
                "market": {"type": "string", "enum": ["CN", "HK", "US", "FUND"], "default": "CN", "description": "市场类型。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "新创建的自选股对象。"},
        "examples": [
            {"title": "添加A股", "arguments": {
                "symbol": "600519", "name": "贵州茅台", "market": "CN"}},
            {"title": "添加基金", "arguments": {"symbol": "161725",
                                            "name": "招商中证白酒指数", "market": "FUND"}},
        ],
    },
    {
        "name": "stocks.update",
        "description": "修改自选股",
        "access": "write",
        "tags": ["stocks", "watchlist", "write"],
        "risk_level": "medium",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "按 stock_id 更新自选股名称。",
            "required": ["stock_id"],
            "properties": {
                "stock_id": {"type": "integer", "description": "自选股 ID。"},
                "name": {"type": "string", "description": "新的股票名称。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "更新后的自选股对象。"},
        "examples": [{"title": "更新名称", "arguments": {"stock_id": 1, "name": "新名称"}}],
    },
    {
        "name": "stocks.delete",
        "description": "删除自选股",
        "access": "write",
        "tags": ["stocks", "watchlist", "write", "dangerous"],
        "risk_level": "high",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "按 stock_id 删除自选股。存在持仓时不允许删除。",
            "required": ["stock_id"],
            "properties": {
                "stock_id": {"type": "integer", "description": "待删除的自选股 ID。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"success": {"type": "boolean"}, "stock_id": {"type": "integer"}}},
        "examples": [{"title": "删除自选股", "arguments": {"stock_id": 10}}],
    },
    {
        "name": "stocks.search",
        "description": "搜索股票/基金",
        "access": "read",
        "tags": ["stocks", "search", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "模糊搜索股票或基金（代码/名称）。",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "搜索关键词。"},
                "market": {"type": "string", "enum": ["", "CN", "HK", "US", "FUND"], "default": "", "description": "市场过滤，空为全部。"},
                "limit": {"type": "integer", "default": 20, "description": "返回数量限制。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"items": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [
            {"title": "搜索茅台", "arguments": {"query": "茅台"}},
            {"title": "搜索基金", "arguments": {"query": "白酒", "market": "FUND"}},
        ],
    },
    {
        "name": "stocks.resolve",
        "description": "按证券代码解析 stock_id",
        "access": "read",
        "tags": ["stocks", "resolve", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "根据已存在于自选股中的证券代码查询 stock_id。",
            "required": ["symbol"],
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，例如 600519。"},
                "market": {
                    "type": "string",
                    "enum": ["", "CN", "HK", "US", "FUND"],
                    "default": "",
                    "description": "可选市场过滤。若同代码跨市场重复，建议传入。",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "description": "解析结果。resolved=true 时可直接取 stock_id。",
            "properties": {
                "resolved": {"type": "boolean", "description": "是否唯一解析成功。"},
                "stock_id": {"type": "integer", "description": "唯一匹配时返回的 stock_id。"},
                "symbol": {"type": "string", "description": "证券代码。"},
                "name": {"type": "string", "description": "证券名称。"},
                "market": {"type": "string", "description": "市场代码。"},
                "candidates": {"type": "array", "description": "存在歧义时返回候选列表。"},
                "count": {"type": "integer", "description": "候选条数。"},
                "message": {"type": "string", "description": "提示信息。"},
            },
        },
        "examples": [
            {"title": "解析A股 stock_id", "arguments": {
                "symbol": "600519", "market": "CN"}},
            {"title": "仅按代码解析", "arguments": {"symbol": "600519"}},
        ],
    },
    {
        "name": "stocks.reorder",
        "description": "批量更新自选股排序",
        "access": "write",
        "tags": ["stocks", "write", "batch"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "批量更新 sort_order。",
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object", "required": ["id", "sort_order"], "properties": {"id": {"type": "integer"}, "sort_order": {"type": "integer"}}},
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"updated": {"type": "integer"}}},
        "examples": [{"title": "批量排序", "arguments": {"items": [{"id": 1, "sort_order": 1}, {"id": 2, "sort_order": 2}]}}],
    },
    # ==================== 账户管理 ====================
    {
        "name": "accounts.list",
        "description": "查询账户列表",
        "access": "read",
        "tags": ["accounts", "portfolio", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {"type": "object", "description": "返回所有账户。", "properties": {}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {"items": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [{"title": "查询账户", "arguments": {}}],
    },
    {
        "name": "accounts.create",
        "description": "创建账户",
        "access": "write",
        "tags": ["accounts", "portfolio", "write"],
        "risk_level": "medium",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "创建新账户。",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "账户名称。"},
                "market": {"type": "string", "default": "CN", "description": "账户市场：CN/HK/US/FUND。"},
                "base_currency": {"type": "string", "default": "CNY", "description": "账户币种：CNY/HKD/USD。"},
                "available_funds": {"type": "number", "default": 0, "description": "可用资金。"},
                "other_funds": {"type": "number", "default": 0, "description": "其他资金（理财等，计入总资产）。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "新创建的账户对象。"},
        "examples": [{"title": "创建账户", "arguments": {"name": "主账户", "market": "CN", "base_currency": "CNY", "available_funds": 100000}}],
    },
    {
        "name": "accounts.update",
        "description": "修改账户",
        "access": "write",
        "tags": ["accounts", "portfolio", "write"],
        "risk_level": "medium",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "按 account_id 更新账户。",
            "required": ["account_id"],
            "properties": {
                "account_id": {"type": "integer", "description": "账户 ID。"},
                "name": {"type": "string", "description": "新的账户名称。"},
                "market": {"type": "string", "description": "账户市场：CN/HK/US/FUND。"},
                "base_currency": {"type": "string", "description": "账户币种：CNY/HKD/USD。"},
                "available_funds": {"type": "number", "description": "新的可用资金。"},
                "other_funds": {"type": "number", "description": "新的其他资金（理财等）。"},
                "enabled": {"type": "boolean", "description": "是否启用。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "更新后的账户对象。"},
        "examples": [{"title": "更新账户", "arguments": {"account_id": 1, "name": "新账户名"}}],
    },
    {
        "name": "accounts.delete",
        "description": "删除账户（会同时删除该账户的所有持仓）",
        "access": "write",
        "tags": ["accounts", "portfolio", "write", "dangerous"],
        "risk_level": "high",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "按 account_id 删除账户及其持仓。",
            "required": ["account_id"],
            "properties": {"account_id": {"type": "integer", "description": "待删除的账户 ID。"}},
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"success": {"type": "boolean"}, "account_id": {"type": "integer"}}},
        "examples": [{"title": "删除账户", "arguments": {"account_id": 1}}],
    },
    # ==================== 新闻资讯 ====================
    {
        "name": "news.list",
        "description": "查询新闻列表",
        "access": "read",
        "tags": ["news", "read"],
        "risk_level": "low",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "获取与自选股相关的新闻。",
            "properties": {
                "symbols": {"type": ["string", "array"], "description": "股票代码列表，逗号分隔或数组。空则获取所有自选股相关新闻。"},
                "hours": {"type": "integer", "default": 168, "description": "时间范围（小时，默认7天）。"},
                "limit": {"type": "integer", "default": 50, "description": "返回数量限制。"},
                "source": {"type": "string", "description": "来源过滤：xueqiu/eastmoney_news/eastmoney，逗号分隔。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"items": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [
            {"title": "近7天新闻", "arguments": {}},
            {"title": "指定股票", "arguments": {"symbols": "600519,000858", "hours": 48}},
        ],
    },
    # ==================== K线与技术指标 ====================
    {
        "name": "klines.get",
        "description": "获取K线数据",
        "access": "read",
        "tags": ["klines", "market", "read"],
        "risk_level": "low",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "获取K线/基金净值走势。",
            "required": ["symbol", "market"],
            "properties": {
                "symbol": {"type": "string", "description": "股票/基金代码。"},
                "market": {"type": "string", "enum": ["CN", "HK", "US", "FUND"], "description": "市场类型。"},
                "days": {"type": "integer", "default": 60, "description": "K线天数/条数。"},
                "interval": {"type": "string", "default": "1d", "description": "周期：1d/1w/1m/1min/5min/15min/30min/60min。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "klines": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [
            {"title": "日K线", "arguments": {
                "symbol": "600519", "market": "CN", "days": 60}},
            {"title": "基金净值", "arguments": {
                "symbol": "161725", "market": "FUND", "days": 90}},
        ],
    },
    {
        "name": "klines.summary",
        "description": "获取技术指标摘要（RSI/MACD/均线等）",
        "access": "read",
        "tags": ["klines", "technical", "read"],
        "risk_level": "low",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "返回技术指标摘要，基金暂不支持。",
            "required": ["symbol", "market"],
            "properties": {
                "symbol": {"type": "string", "description": "股票代码。"},
                "market": {"type": "string", "enum": ["CN", "HK", "US"], "description": "市场类型。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "包含 RSI、MACD、布林带、均线等指标。"},
        "examples": [{"title": "技术指标", "arguments": {"symbol": "600519", "market": "CN"}}],
    },
    # ==================== 分析历史 ====================
    {
        "name": "history.list",
        "description": "查询分析历史列表",
        "access": "read",
        "tags": ["history", "analysis", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "获取AI分析历史列表。",
            "properties": {
                "agent_name": {"type": "string", "description": "Agent名称过滤。"},
                "stock_symbol": {"type": "string", "description": "股票代码过滤。"},
                "kind": {"type": "string", "enum": ["workflow", "capability"], "default": "workflow", "description": "Agent类型过滤。"},
                "limit": {"type": "integer", "default": 30, "description": "返回数量限制（最大100）。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"items": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [{"title": "最近分析", "arguments": {"limit": 10}}],
    },
    {
        "name": "history.get",
        "description": "获取单条分析详情",
        "access": "read",
        "tags": ["history", "analysis", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "按ID获取分析详情。",
            "required": ["history_id"],
            "properties": {"history_id": {"type": "integer", "description": "分析历史ID。"}},
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "分析详情，包含content和suggestions。"},
        "examples": [{"title": "获取详情", "arguments": {"history_id": 1}}],
    },
    # ==================== 建议池 ====================
    {
        "name": "suggestions.latest",
        "description": "获取各股票最新建议",
        "access": "read",
        "tags": ["suggestions", "analysis", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "每只股票返回最新一条有效建议。",
            "properties": {
                "symbols": {"type": ["string", "array"], "description": "股票代码列表，逗号分隔或数组。空则返回所有自选股建议。"},
                "include_expired": {"type": "boolean", "default": False, "description": "是否包含已过期建议。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"items": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [{"title": "最新建议", "arguments": {}}],
    },
    {
        "name": "suggestions.stock",
        "description": "查询某只股票的历史建议",
        "access": "read",
        "tags": ["suggestions", "analysis", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "获取指定股票的建议历史。",
            "required": ["symbol"],
            "properties": {
                "symbol": {"type": "string", "description": "股票代码。"},
                "market": {"type": "string", "description": "市场代码（可选）。"},
                "include_expired": {"type": "boolean", "default": False, "description": "是否包含已过期建议。"},
                "limit": {"type": "integer", "default": 10, "description": "返回数量限制。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"items": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [{"title": "股票建议历史", "arguments": {"symbol": "600519", "limit": 5}}],
    },
    # ==================== Agent 操作 ====================
    {
        "name": "agents.list",
        "description": "查询Agent配置列表",
        "access": "read",
        "tags": ["agents", "config", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "返回Agent配置信息。",
            "properties": {
                "kind": {"type": "string", "enum": ["", "workflow", "capability"], "default": "", "description": "Agent类型过滤。"},
                "include_internal": {"type": "boolean", "default": False, "description": "是否包含内部Agent。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"items": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [{"title": "所有Agent", "arguments": {}}],
    },
    {
        "name": "agents.health",
        "description": "Agent调度健康状态",
        "access": "read",
        "tags": ["agents", "diagnostic", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "返回Agent调度健康概览。",
            "properties": {
                "include_internal": {"type": "boolean", "default": False, "description": "是否包含内部Agent。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"timezone": {"type": "string"}, "summary": {"type": "object"}, "agents": {"type": "array"}}},
        "examples": [{"title": "健康状态", "arguments": {}}],
    },
    {
        "name": "agents.trigger",
        "description": "触发Agent执行",
        "access": "write",
        "tags": ["agents", "execution", "write"],
        "risk_level": "medium",
        "cost_hint": "high",
        "inputSchema": {
            "type": "object",
            "description": "异步触发指定Agent执行。",
            "required": ["agent_name"],
            "properties": {
                "agent_name": {"type": "string", "description": "Agent名称。"},
                "symbol": {"type": "string", "description": "股票代码（单只执行时）。"},
                "market": {"type": "string", "description": "市场类型（单只执行时）。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"triggered": {"type": "boolean"}, "agent_name": {"type": "string"}, "message": {"type": "string"}}},
        "examples": [
            {"title": "触发日报", "arguments": {"agent_name": "daily_report"}},
            {"title": "单只分析", "arguments": {
                "agent_name": "chart_analyst", "symbol": "600519", "market": "CN"}},
        ],
    },
    # ==================== 价格提醒 ====================
    {
        "name": "price_alerts.list",
        "description": "查询价格提醒规则",
        "access": "read",
        "tags": ["price_alerts", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "返回价格提醒规则列表。",
            "properties": {
                "stock_id": {"type": "integer", "description": "按股票ID过滤。"},
                "enabled_only": {"type": "boolean", "default": False, "description": "仅返回启用的规则。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"items": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [{"title": "所有规则", "arguments": {}}],
    },
    {
        "name": "price_alerts.create",
        "description": "创建价格提醒规则",
        "access": "write",
        "tags": ["price_alerts", "write"],
        "risk_level": "medium",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "为指定股票创建价格提醒。",
            "required": ["stock_id", "condition_group"],
            "properties": {
                "stock_id": {"type": "integer", "description": "股票ID。"},
                "name": {"type": "string", "description": "规则名称。"},
                "enabled": {"type": "boolean", "default": True, "description": "是否启用。"},
                "condition_group": {
                    "type": "object",
                    "description": "条件组：{op: 'and'|'or', items: [{type, op, value}]}",
                    "properties": {
                        "op": {"type": "string", "enum": ["and", "or"], "description": "逻辑运算符。"},
                        "items": {"type": "array", "description": "条件项列表。"},
                    },
                },
                "market_hours_mode": {"type": "string", "default": "trading_only", "description": "市场时段：trading_only/all_hours。"},
                "cooldown_minutes": {"type": "integer", "default": 30, "description": "冷却时间（分钟）。"},
                "max_triggers_per_day": {"type": "integer", "default": 3, "description": "每日最大触发次数。"},
                "repeat_mode": {"type": "string", "default": "repeat", "description": "重复模式：repeat/once。"},
                "expire_at": {"type": "string", "description": "过期时间（ISO格式）。"},
                "notify_channel_ids": {"type": "array", "items": {"type": "integer"}, "description": "通知渠道ID列表。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "新创建的提醒规则。"},
        "examples": [{"title": "价格突破提醒", "arguments": {"stock_id": 1, "condition_group": {"op": "and", "items": [{"type": "price", "op": ">=", "value": 100}]}}}],
    },
    {
        "name": "price_alerts.update",
        "description": "修改价格提醒规则",
        "access": "write",
        "tags": ["price_alerts", "write"],
        "risk_level": "medium",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "按rule_id更新提醒规则。",
            "required": ["rule_id"],
            "properties": {
                "rule_id": {"type": "integer", "description": "规则ID。"},
                "name": {"type": "string", "description": "新名称。"},
                "enabled": {"type": "boolean", "description": "是否启用。"},
                "condition_group": {"type": "object", "description": "新的条件组。"},
                "cooldown_minutes": {"type": "integer", "description": "新的冷却时间。"},
                "max_triggers_per_day": {"type": "integer", "description": "新的每日最大触发次数。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "更新后的提醒规则。"},
        "examples": [{"title": "更新规则", "arguments": {"rule_id": 1, "enabled": False}}],
    },
    {
        "name": "price_alerts.delete",
        "description": "删除价格提醒规则",
        "access": "write",
        "tags": ["price_alerts", "write", "dangerous"],
        "risk_level": "medium",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "按rule_id删除提醒规则。",
            "required": ["rule_id"],
            "properties": {"rule_id": {"type": "integer", "description": "规则ID。"}},
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"success": {"type": "boolean"}, "rule_id": {"type": "integer"}}},
        "examples": [{"title": "删除规则", "arguments": {"rule_id": 1}}],
    },
    {
        "name": "price_alerts.toggle",
        "description": "启用/禁用价格提醒",
        "access": "write",
        "tags": ["price_alerts", "write"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "切换提醒规则的启用状态。",
            "required": ["rule_id", "enabled"],
            "properties": {
                "rule_id": {"type": "integer", "description": "规则ID。"},
                "enabled": {"type": "boolean", "description": "是否启用。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "更新后的提醒规则。"},
        "examples": [{"title": "禁用规则", "arguments": {"rule_id": 1, "enabled": False}}],
    },
    {
        "name": "price_alerts.scan",
        "description": "立即扫描价格提醒",
        "access": "read",
        "tags": ["price_alerts", "execution", "read"],
        "risk_level": "low",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "执行一次价格提醒扫描检查。",
            "properties": {
                "dry_run": {"type": "boolean", "default": False, "description": "是否仅模拟（不实际触发通知）。"},
                "bypass_market_hours": {"type": "boolean", "default": True, "description": "是否忽略市场时段限制。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "扫描结果。"},
        "examples": [{"title": "扫描测试", "arguments": {"dry_run": True}}],
    },
    # ==================== 基金专用 ====================
    {
        "name": "funds.overview",
        "description": "获取基金概览（重仓股+业绩走势）",
        "access": "read",
        "tags": ["funds", "read"],
        "risk_level": "low",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "返回基金重仓前10和业绩走势。",
            "required": ["fund_code"],
            "properties": {"fund_code": {"type": "string", "description": "基金代码。"}},
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"fund_code": {"type": "string"}, "top_holdings": {"type": "array"}, "performance": {"type": "object"}}},
        "examples": [{"title": "基金概览", "arguments": {"fund_code": "161725"}}],
    },
    {
        "name": "funds.holdings",
        "description": "获取基金重仓股",
        "access": "read",
        "tags": ["funds", "read"],
        "risk_level": "low",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "返回基金重仓股列表。",
            "required": ["fund_code"],
            "properties": {
                "fund_code": {"type": "string", "description": "基金代码。"},
                "topline": {"type": "integer", "default": 10, "description": "返回前N只重仓股。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"fund_code": {"type": "string"}, "holdings": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [{"title": "重仓股", "arguments": {"fund_code": "161725", "topline": 10}}],
    },
    # ==================== 工具类 ====================
    {
        "name": "exchange_rates.get",
        "description": "获取汇率（HKD/USD兑CNY）",
        "access": "read",
        "tags": ["utils", "exchange", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {"type": "object", "description": "返回实时汇率。", "properties": {}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {"HKD_CNY": {"type": "number"}, "USD_CNY": {"type": "number"}, "updated_at": {"type": "integer"}}},
        "examples": [{"title": "获取汇率", "arguments": {}}],
    },
    {
        "name": "quotes.get",
        "description": "获取单只股票/基金行情",
        "access": "read",
        "tags": ["quotes", "market", "read"],
        "risk_level": "low",
        "cost_hint": "low",
        "inputSchema": {
            "type": "object",
            "description": "返回指定股票/基金的实时行情。",
            "required": ["symbol", "market"],
            "properties": {
                "symbol": {"type": "string", "description": "股票/基金代码。"},
                "market": {"type": "string", "enum": ["CN", "HK", "US", "FUND"], "description": "市场类型。"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "description": "行情数据。"},
        "examples": [
            {"title": "A股行情", "arguments": {"symbol": "600519", "market": "CN"}},
            {"title": "基金估值", "arguments": {"symbol": "161725", "market": "FUND"}},
        ],
    },
    {
        "name": "quotes.batch",
        "description": "批量获取行情",
        "access": "read",
        "tags": ["quotes", "market", "read", "batch"],
        "risk_level": "low",
        "cost_hint": "medium",
        "inputSchema": {
            "type": "object",
            "description": "批量获取多只股票/基金的行情。",
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "description": "行情请求列表。",
                    "items": {"type": "object", "required": ["symbol", "market"], "properties": {"symbol": {"type": "string"}, "market": {"type": "string"}}},
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"quotes": {"type": "array"}, "count": {"type": "integer"}}},
        "examples": [{"title": "批量行情", "arguments": {"items": [{"symbol": "600519", "market": "CN"}, {"symbol": "161725", "market": "FUND"}]}}],
    },
]


def _call_tool(
    name: str,
    arguments: dict[str, Any],
    db: Session,
    principal: dict[str, Any],
) -> dict[str, Any]:
    # 持仓管理
    if name == "positions.list":
        return _list_positions(arguments, db)
    if name == "positions.create":
        return _create_position(arguments, db)
    if name == "positions.update":
        return _update_position(arguments, db)
    if name == "positions.trade":
        return _trade_position(arguments, db)
    if name == "positions.trades.list":
        return _list_position_trades(arguments, db)
    if name == "positions.delete":
        return _delete_position(arguments, db)
    if name == "positions.reorder.batch":
        return _reorder_positions(arguments, db)
    if name == "portfolio.summary":
        return _portfolio_summary(arguments, db)

    # 自选股管理
    if name == "stocks.list":
        return _list_watchlist(arguments, db)
    if name == "stocks.quotes":
        return _watchlist_quotes(arguments, db)
    if name == "stocks.create":
        return _create_stock(arguments, db)
    if name == "stocks.update":
        return _update_stock(arguments, db)
    if name == "stocks.delete":
        return _delete_stock(arguments, db)
    if name == "stocks.search":
        return _search_stocks(arguments, db)
    if name == "stocks.resolve":
        return _resolve_stock(arguments, db)
    if name == "stocks.reorder":
        return _reorder_stocks(arguments, db)

    # 账户管理
    if name == "accounts.list":
        return _list_accounts(arguments, db)
    if name == "accounts.create":
        return _create_account(arguments, db)
    if name == "accounts.update":
        return _update_account(arguments, db)
    if name == "accounts.delete":
        return _delete_account(arguments, db)

    # 新闻资讯
    if name == "news.list":
        return _list_news(arguments, db)

    # K线与技术指标
    if name == "klines.get":
        return _get_klines(arguments, db)
    if name == "klines.summary":
        return _get_kline_summary(arguments, db)

    # 分析历史
    if name == "history.list":
        return _list_history(arguments, db)
    if name == "history.get":
        return _get_history(arguments, db)

    # 建议池
    if name == "suggestions.latest":
        return _get_latest_suggestions(arguments, db)
    if name == "suggestions.stock":
        return _get_stock_suggestions(arguments, db)

    # Agent 操作
    if name == "agents.list":
        return _list_agents(arguments, db)
    if name == "agents.health":
        return _agents_health(arguments, db)
    if name == "agents.trigger":
        return _trigger_agent(arguments, db)

    # 价格提醒
    if name == "price_alerts.list":
        return _list_price_alerts(arguments, db)
    if name == "price_alerts.create":
        return _create_price_alert(arguments, db)
    if name == "price_alerts.update":
        return _update_price_alert(arguments, db)
    if name == "price_alerts.delete":
        return _delete_price_alert(arguments, db)
    if name == "price_alerts.toggle":
        return _toggle_price_alert(arguments, db)
    if name == "price_alerts.scan":
        return _scan_price_alerts(arguments, db)

    # 基金专用
    if name == "funds.overview":
        return _fund_overview(arguments, db)
    if name == "funds.holdings":
        return _fund_holdings(arguments, db)

    # 工具类
    if name == "exchange_rates.get":
        return _get_exchange_rates(arguments, db)
    if name == "quotes.get":
        return _get_quote(arguments, db)
    if name == "quotes.batch":
        return _get_quotes_batch(arguments, db)

    # 市场与诊断
    if name == "market.indices":
        return _market_indices(arguments, db)
    if name == "dashboard.overview":
        return _dashboard_overview(arguments, db)
    if name == "mcp.health":
        return _mcp_health(arguments, db)
    if name == "mcp.auth.status":
        return _mcp_auth_status(arguments, principal)
    if name == "mcp.version":
        return _mcp_version(arguments, db)
    if name == "mcp.logs.query":
        return _mcp_logs_query(arguments, db)

    raise McpToolError(
        error_code=ERR_NOT_FOUND,
        message=f"未知工具: {name}",
        hint="请先调用 tools/list 获取可用工具名称",
        details={"tool": name},
    )


@router.post("")
def mcp_rpc(
    body: dict[str, Any],
    principal: dict[str, Any] = Depends(require_mcp_user),
    db: Session = Depends(get_db),
):
    request_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if body.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return _jsonrpc_error(request_id, -32600, "Invalid Request")

    if method == "initialize":
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "panwatch-mcp",
                    "version": MCP_SERVER_VERSION,
                },
            },
        )

    if method == "notifications/initialized":
        return _jsonrpc_result(request_id, {})

    if method == "tools/list":
        return _jsonrpc_result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(tool_name, str):
            return _jsonrpc_error(
                request_id,
                -32602,
                "Invalid params",
                _build_error_data(
                    error_code=ERR_INVALID_PARAMS,
                    hint="请提供字符串类型的 name 字段",
                    retryable=False,
                ),
            )
        if not isinstance(arguments, dict):
            return _jsonrpc_error(
                request_id,
                -32602,
                "Invalid params",
                _build_error_data(
                    error_code=ERR_INVALID_PARAMS,
                    hint="arguments 必须是对象，例如 {}",
                    retryable=False,
                ),
            )

        started = time.perf_counter()
        is_write_tool = tool_name in WRITE_TOOL_NAMES
        try:
            result = _call_tool(tool_name, arguments, db, principal)
            if is_write_tool:
                _audit_write_tool(
                    principal=principal,
                    tool_name=tool_name,
                    arguments=arguments,
                    status_text="success",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            return _jsonrpc_result(request_id, _mcp_content(result))
        except McpToolError as e:
            db.rollback()
            if is_write_tool:
                _audit_write_tool(
                    principal=principal,
                    tool_name=tool_name,
                    arguments=arguments,
                    status_text=f"error:{e.error_code}",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            code = -32602 if e.error_code == ERR_INVALID_PARAMS else -32000
            if e.error_code == ERR_NOT_FOUND:
                code = -32004
            elif e.error_code == ERR_CONFLICT:
                code = -32009
            return _jsonrpc_error(
                request_id,
                code,
                e.message,
                _build_error_data(
                    error_code=e.error_code,
                    hint=e.hint,
                    retryable=e.retryable,
                    details=e.details,
                ),
            )
        except Exception as e:
            db.rollback()
            if is_write_tool:
                _audit_write_tool(
                    principal=principal,
                    tool_name=tool_name,
                    arguments=arguments,
                    status_text="error:internal",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            logger.exception("MCP tools/call failed: tool=%s", tool_name)
            return _jsonrpc_error(
                request_id,
                -32603,
                "Internal error",
                _build_error_data(
                    error_code=ERR_INTERNAL,
                    hint="请检查参数并稍后重试，若持续失败请联系管理员",
                    retryable=True,
                    details={"exception": str(e)},
                ),
            )

    return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")
