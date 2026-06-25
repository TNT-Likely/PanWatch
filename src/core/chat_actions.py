"""AI 对话操作：提议、确认与执行。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from src.core.long_term_plan import evaluate_add_plan
from src.core.position_trades_context import fetch_recent_trades
from src.web.models import (
    Account,
    ChatPendingAction,
    NotifyChannel,
    Position,
    PositionTrade,
    PriceAlertRule,
    Stock,
)

logger = logging.getLogger(__name__)

CHAT_ACTION_SETTINGS = {
    "create_price_alert": "chat_action_create_alert",
    "add_position": "chat_action_add_position",
    "reduce_position": "chat_action_reduce_position",
}

DEFAULT_PERMISSIONS: dict[str, bool] = {
    "chat_action_create_alert": True,
    "chat_action_add_position": False,
    "chat_action_reduce_position": False,
}

ACTION_EXPIRE_MINUTES = 30

CONDITION_OP_LABELS = {
    ">=": "≥",
    "<=": "≤",
    ">": ">",
    "<": "<",
    "==": "=",
}


def _calc_weighted_cost(
    cur_qty: int, cur_cost: float, add_qty: int, add_price: float
) -> tuple[int, float]:
    new_qty = int(cur_qty) + int(add_qty)
    if new_qty <= 0:
        raise ValueError("加仓后股数必须大于 0")
    if cur_qty <= 0 or cur_cost <= 0:
        return new_qty, float(add_price)
    total_cost = cur_qty * cur_cost + add_qty * add_price
    return new_qty, round(total_cost / new_qty, 6)


def _validate_condition_group(group) -> None:
    from fastapi import HTTPException

    if group.op not in ("and", "or"):
        raise HTTPException(400, "condition_group.op 仅支持 and/or")
    if not group.items:
        raise HTTPException(400, "condition_group.items 不能为空")
    allowed_types = {"price", "change_pct", "turnover", "volume", "volume_ratio"}
    allowed_ops = {">=", "<=", ">", "<", "==", "=", "!=", "<>", "between", "in"}
    for it in group.items:
        if it.type not in allowed_types:
            raise HTTPException(400, f"不支持的条件类型: {it.type}")
        if it.op not in allowed_ops:
            raise HTTPException(400, f"不支持的操作符: {it.op}")


def _setting_bool(db: Session, key: str, default: bool) -> bool:
    from src.web.models import AppSettings

    row = db.query(AppSettings).filter(AppSettings.key == key).first()
    if not row or row.value is None:
        return default
    return str(row.value).strip().lower() in ("1", "true", "yes", "on")


def get_chat_action_permissions(db: Session) -> dict[str, bool]:
    return {
        action: _setting_bool(db, setting_key, DEFAULT_PERMISSIONS.get(setting_key, False))
        for action, setting_key in CHAT_ACTION_SETTINGS.items()
    }


def build_action_tools(permissions: dict[str, bool]) -> list[dict]:
    tools: list[dict] = []
    if permissions.get("create_price_alert"):
        tools.extend([
            {
                "type": "function",
                "function": {
                    "name": "list_notify_channels",
                    "description": "列出用户已启用的通知渠道，建提醒前可先查看可用渠道。",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_price_alerts",
                    "description": "列出用户的价格提醒规则，可按股票筛选。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "可选，股票代码"},
                            "market": {"type": "string", "description": "市场代码 CN/HK/US", "default": "CN"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "propose_create_price_alert",
                    "description": (
                        "提议创建价格提醒（不会立即生效，需用户确认）。"
                        "当用户说「提醒我」「建个提醒」「到XX价通知我」时使用。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "股票代码"},
                            "market": {"type": "string", "description": "市场 CN/HK/US", "default": "CN"},
                            "condition_type": {
                                "type": "string",
                                "enum": ["price", "change_pct"],
                                "description": "price=价格, change_pct=涨跌幅(%)",
                            },
                            "op": {"type": "string", "enum": [">=", "<=", ">", "<"], "description": "比较运算符"},
                            "value": {"type": "number", "description": "阈值"},
                            "name": {"type": "string", "description": "提醒名称，可选"},
                        },
                        "required": ["symbol", "condition_type", "op", "value"],
                    },
                },
            },
        ])
    if permissions.get("add_position"):
        tools.append({
            "type": "function",
            "function": {
                "name": "propose_add_position",
                "description": (
                    "提议记录加仓（不会立即生效，需用户确认）。"
                    "当用户明确要求加仓、买入、加仓位时使用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "股票代码"},
                        "market": {"type": "string", "description": "市场 CN/HK/US", "default": "CN"},
                        "quantity": {"type": "integer", "description": "加仓股数"},
                        "price": {"type": "number", "description": "买入单价，不填则用现价"},
                        "account_name": {"type": "string", "description": "账户名称，多账户时必填"},
                        "note": {"type": "string", "description": "备注，可选"},
                    },
                    "required": ["symbol", "quantity"],
                },
            },
        })
    if permissions.get("reduce_position"):
        tools.append({
            "type": "function",
            "function": {
                "name": "propose_reduce_position",
                "description": (
                    "提议记录减仓/卖出（不会立即生效，需用户确认）。"
                    "当用户明确要求减仓、卖出、减仓位时使用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "股票代码"},
                        "market": {"type": "string", "description": "市场 CN/HK/US", "default": "CN"},
                        "quantity": {"type": "integer", "description": "卖出股数"},
                        "price": {"type": "number", "description": "卖出单价，不填则用现价"},
                        "account_name": {"type": "string", "description": "账户名称，多账户时必填"},
                        "note": {"type": "string", "description": "备注，可选"},
                    },
                    "required": ["symbol", "quantity"],
                },
            },
        })
    return tools


def build_action_system_addendum(permissions: dict[str, bool]) -> str:
    lines = ["\n\n--- 操作权限 ---"]
    if permissions.get("create_price_alert"):
        lines.append(
            "- 用户要求建提醒时：先确认股票与条件，再调用 propose_create_price_alert，"
            "告知用户需在对话中点击确认后才会创建。"
        )
    else:
        lines.append("- 当前未开启「对话建提醒」权限，仅可告知用户去价格提醒页手动创建。")

    if permissions.get("add_position"):
        lines.append(
            "- 用户要求加仓时：先查持仓与今日流水，再调用 propose_add_position；"
            "A 股股数须为 100 的整数倍；今日已买入则不要重复提议。"
        )
    else:
        lines.append("- 当前未开启「对话加仓」权限。")

    if permissions.get("reduce_position"):
        lines.append(
            "- 用户要求减仓时：先查持仓与今日流水，再调用 propose_reduce_position；"
            "今日已卖出则不要重复提议。"
        )
    else:
        lines.append("- 当前未开启「对话减仓」权限。")

    lines.append("- 所有写操作仅生成待确认提议，不得声称已执行。")
    return "\n".join(lines)


def _find_stock(db: Session, symbol: str, market: str) -> Stock | None:
    sym = (symbol or "").strip()
    mkt = (market or "CN").strip().upper()
    if not sym:
        return None
    return (
        db.query(Stock)
        .filter(Stock.symbol == sym, Stock.market == mkt)
        .first()
    )


def _find_positions(db: Session, symbol: str, market: str) -> list[Position]:
    return (
        db.query(Position)
        .join(Stock, Position.stock_id == Stock.id)
        .filter(Stock.symbol == symbol, Stock.market == market)
        .all()
    )


def _resolve_position(
    db: Session,
    symbol: str,
    market: str,
    account_name: str | None,
) -> tuple[Position | None, str | None]:
    positions = _find_positions(db, symbol, market)
    if not positions:
        return None, f"未找到 {market}:{symbol} 的持仓，请先在持仓页建仓。"
    if account_name:
        name = account_name.strip()
        for p in positions:
            if p.account and p.account.name == name:
                return p, None
        accounts = ", ".join(p.account.name for p in positions if p.account)
        return None, f"未找到账户「{name}」，可选账户：{accounts}"
    if len(positions) == 1:
        return positions[0], None
    accounts = ", ".join(p.account.name for p in positions if p.account)
    return None, f"该股票在多个账户有持仓，请指定 account_name。可选：{accounts}"


def _default_notify_channel_ids(db: Session) -> list[int]:
    channels = (
        db.query(NotifyChannel)
        .filter(NotifyChannel.enabled == True)  # noqa: E712
        .order_by(NotifyChannel.is_default.desc(), NotifyChannel.id.asc())
        .all()
    )
    if not channels:
        return []
    default = next((c for c in channels if c.is_default), channels[0])
    return [default.id]


def _format_condition_label(condition_type: str, op: str, value: float) -> str:
    op_label = CONDITION_OP_LABELS.get(op, op)
    if condition_type == "change_pct":
        return f"涨跌幅 {op_label} {value}%"
    return f"价格 {op_label} {value}"


def _tool_result(ok: bool, **kwargs: Any) -> str:
    payload = {"ok": ok, **kwargs}
    return json.dumps(payload, ensure_ascii=False)


def _create_pending(
    db: Session,
    *,
    conversation_id: int,
    action_type: str,
    payload: dict,
    preview: dict,
) -> ChatPendingAction:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    action = ChatPendingAction(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        action_type=action_type,
        payload=payload,
        preview=preview,
        status="pending",
        expires_at=now + timedelta(minutes=ACTION_EXPIRE_MINUTES),
    )
    db.add(action)
    db.flush()
    return action


def list_notify_channels_tool(db: Session) -> str:
    channels = (
        db.query(NotifyChannel)
        .filter(NotifyChannel.enabled == True)  # noqa: E712
        .order_by(NotifyChannel.is_default.desc(), NotifyChannel.id.asc())
        .all()
    )
    if not channels:
        return "用户暂无已启用的通知渠道，请先在设置页配置。"
    lines = [
        f"- ID {c.id}: {c.name} ({c.type}){' [默认]' if c.is_default else ''}"
        for c in channels
    ]
    return "可用通知渠道：\n" + "\n".join(lines)


def list_price_alerts_tool(db: Session, symbol: str | None, market: str) -> str:
    q = db.query(PriceAlertRule, Stock).join(Stock, PriceAlertRule.stock_id == Stock.id)
    if symbol:
        q = q.filter(Stock.symbol == symbol, Stock.market == market)
    rows = q.order_by(PriceAlertRule.id.desc()).limit(20).all()
    if not rows:
        return "暂无价格提醒规则。"
    lines: list[str] = []
    for rule, stock in rows:
        items = (rule.condition_group or {}).get("items") or []
        cond = "；".join(
            _format_condition_label(it.get("type", "price"), it.get("op", ">="), float(it.get("value", 0)))
            for it in items[:2]
        )
        status = "启用" if rule.enabled else "停用"
        lines.append(f"- [{status}] {stock.name}({stock.market}:{stock.symbol}) {rule.name or ''} {cond}")
    return "价格提醒：\n" + "\n".join(lines)


def propose_create_price_alert(
    db: Session,
    *,
    conversation_id: int,
    args: dict,
) -> tuple[str, str | None]:
    from src.web.api.price_alerts import AlertConditionGroup

    symbol = (args.get("symbol") or "").strip()
    market = (args.get("market") or "CN").strip().upper()
    condition_type = (args.get("condition_type") or "price").strip()
    op = (args.get("op") or ">=").strip()
    value = args.get("value")
    name = (args.get("name") or "").strip()

    stock = _find_stock(db, symbol, market)
    if not stock:
        return _tool_result(False, error=f"未找到股票 {market}:{symbol}，请确认代码或在自选股中添加。"), None

    if value is None:
        return _tool_result(False, error="缺少阈值 value"), None

    channel_ids = _default_notify_channel_ids(db)
    if not channel_ids:
        return _tool_result(
            False,
            error="未配置通知渠道，请先在设置页添加并启用通知渠道。",
        ), None

    try:
        group = AlertConditionGroup(
            op="and",
            items=[{"type": condition_type, "op": op, "value": float(value)}],
        )
        _validate_condition_group(group)
    except Exception as e:
        return _tool_result(False, error=str(e)), None

    cond_label = _format_condition_label(condition_type, op, float(value))
    rule_name = name or f"{stock.name} {cond_label} 提醒"
    channels = db.query(NotifyChannel).filter(NotifyChannel.id.in_(channel_ids)).all()
    channel_label = "、".join(c.name for c in channels) or "默认渠道"

    payload = {
        "stock_id": stock.id,
        "name": rule_name,
        "condition_group": group.model_dump(),
        "market_hours_mode": "trading_only",
        "cooldown_minutes": 30,
        "max_triggers_per_day": 3,
        "repeat_mode": "repeat",
        "expire_at": None,
        "notify_channel_ids": channel_ids,
    }
    preview = {
        "title": "创建价格提醒",
        "lines": [
            f"{stock.name} ({market}:{symbol})",
            cond_label,
            f"通知：{channel_label}",
            "触发：仅交易时段",
        ],
    }
    action = _create_pending(
        db,
        conversation_id=conversation_id,
        action_type="create_price_alert",
        payload=payload,
        preview=preview,
    )
    return (
        _tool_result(
            True,
            action_id=action.id,
            preview=preview,
            message="已生成待确认的价格提醒，请用户在对话卡片中点击「确认」后才会创建。",
        ),
        action.id,
    )


def _has_today_trade(db: Session, position_id: int, side: str) -> bool:
    trades = fetch_recent_trades(db, today_only=True, limit=50)
    return any(t.get("position_id") == position_id and t.get("side") == side for t in trades)


def _normalize_cn_quantity(qty: int, market: str) -> tuple[int, str | None]:
    q = int(qty)
    if q <= 0:
        return 0, "股数必须大于 0"
    if market.upper() == "CN" and q % 100 != 0:
        return 0, "A 股加仓/减仓股数须为 100 的整数倍"
    return q, None


async def _resolve_trade_price(symbol: str, market: str, price: float | None) -> tuple[float | None, str | None]:
    if price is not None and float(price) > 0:
        return float(price), None
    try:
        import asyncio

        from src.collectors.akshare_collector import _fetch_tencent_quotes, _tencent_symbol
        from src.models.market import MarketCode

        mc = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
        tsym = _tencent_symbol(symbol, mc)
        rows = await asyncio.to_thread(_fetch_tencent_quotes, [tsym])
        if rows:
            qprice = rows[0].get("current_price")
            if qprice and float(qprice) > 0:
                return float(qprice), None
    except Exception as e:
        logger.debug("resolve trade price failed: %s", e)
    return None, "无法获取现价，请指定 price"


async def propose_add_position(
    db: Session,
    *,
    conversation_id: int,
    args: dict,
) -> tuple[str, str | None]:
    symbol = (args.get("symbol") or "").strip()
    market = (args.get("market") or "CN").strip().upper()
    quantity = args.get("quantity")
    account_name = args.get("account_name")
    note = (args.get("note") or "").strip() or "AI 助手加仓"

    qty, qty_err = _normalize_cn_quantity(int(quantity or 0), market)
    if qty_err:
        return _tool_result(False, error=qty_err), None

    position, err = _resolve_position(db, symbol, market, account_name)
    if err or not position:
        return _tool_result(False, error=err or "持仓不存在"), None

    if _has_today_trade(db, position.id, "buy"):
        return _tool_result(False, error="该持仓今日已有买入记录，不应重复加仓。"), None

    price, price_err = await _resolve_trade_price(symbol, market, args.get("price"))
    if price_err or not price:
        return _tool_result(False, error=price_err or "缺少价格"), None

    stock = position.stock
    warnings: list[str] = []
    if stock and stock.investment_profile:
        add_eval = evaluate_add_plan(
            stock.investment_profile,
            current_price=price,
            avg_cost=float(position.cost_price or 0),
            has_buy_today=False,
            market=market,
        )
        if add_eval.get("blockers"):
            warnings.extend(add_eval["blockers"])

    cost_before = float(position.cost_price or 0)
    qty_before = int(position.quantity or 0)
    try:
        new_qty, new_cost = _calc_weighted_cost(qty_before, cost_before, qty, price)
    except ValueError as e:
        return _tool_result(False, error=str(e)), None

    preview_lines = [
        f"{stock.name if stock else symbol} ({market}:{symbol})",
        f"账户：{position.account.name if position.account else '默认'}",
        f"加仓 {qty} 股 @ {price}",
        f"预计：{qty_before} 股 → {new_qty} 股，成本 {cost_before:.4f} → {new_cost:.4f}",
    ]
    if warnings:
        preview_lines.append(f"注意：{'；'.join(warnings)}")

    payload = {
        "position_id": position.id,
        "price": price,
        "quantity": qty,
        "note": note,
    }
    preview = {"title": "记录加仓", "lines": preview_lines, "warnings": warnings}
    action = _create_pending(
        db,
        conversation_id=conversation_id,
        action_type="add_position",
        payload=payload,
        preview=preview,
    )
    return (
        _tool_result(
            True,
            action_id=action.id,
            preview=preview,
            message="已生成待确认的加仓操作，请用户点击「确认」后才会记录流水。",
        ),
        action.id,
    )


async def propose_reduce_position(
    db: Session,
    *,
    conversation_id: int,
    args: dict,
) -> tuple[str, str | None]:
    symbol = (args.get("symbol") or "").strip()
    market = (args.get("market") or "CN").strip().upper()
    quantity = args.get("quantity")
    account_name = args.get("account_name")
    note = (args.get("note") or "").strip() or "AI 助手减仓"

    qty, qty_err = _normalize_cn_quantity(int(quantity or 0), market)
    if qty_err:
        return _tool_result(False, error=qty_err), None

    position, err = _resolve_position(db, symbol, market, account_name)
    if err or not position:
        return _tool_result(False, error=err or "持仓不存在"), None

    qty_before = int(position.quantity or 0)
    if qty > qty_before:
        return _tool_result(False, error=f"卖出股数 {qty} 超过持仓 {qty_before}"), None

    if _has_today_trade(db, position.id, "sell"):
        return _tool_result(False, error="该持仓今日已有卖出记录，不应重复减仓。"), None

    price, price_err = await _resolve_trade_price(symbol, market, args.get("price"))
    if price_err or not price:
        return _tool_result(False, error=price_err or "缺少价格"), None

    stock = position.stock
    cost_before = float(position.cost_price or 0)
    new_qty = qty_before - qty
    pnl = (price - cost_before) * qty

    preview_lines = [
        f"{stock.name if stock else symbol} ({market}:{symbol})",
        f"账户：{position.account.name if position.account else '默认'}",
        f"减仓 {qty} 股 @ {price}",
        f"预计：{qty_before} 股 → {new_qty} 股，本次盈亏 {pnl:+.2f}",
    ]

    payload = {
        "position_id": position.id,
        "price": price,
        "quantity": qty,
        "note": note,
    }
    preview = {"title": "记录减仓", "lines": preview_lines}
    action = _create_pending(
        db,
        conversation_id=conversation_id,
        action_type="reduce_position",
        payload=payload,
        preview=preview,
    )
    return (
        _tool_result(
            True,
            action_id=action.id,
            preview=preview,
            message="已生成待确认的减仓操作，请用户点击「确认」后才会记录流水。",
        ),
        action.id,
    )


def _execute_create_price_alert(db: Session, payload: dict) -> dict:
    from src.web.api.price_alerts import AlertConditionGroup

    stock = db.query(Stock).filter(Stock.id == payload["stock_id"]).first()
    if not stock:
        raise ValueError("股票不存在")
    group = AlertConditionGroup(**payload["condition_group"])
    _validate_condition_group(group)
    row = PriceAlertRule(
        stock_id=payload["stock_id"],
        name=(payload.get("name") or "").strip() or f"{stock.name} 提醒",
        enabled=True,
        condition_group=group.model_dump(),
        market_hours_mode=payload.get("market_hours_mode") or "trading_only",
        cooldown_minutes=max(0, int(payload.get("cooldown_minutes") or 30)),
        max_triggers_per_day=max(0, int(payload.get("max_triggers_per_day") or 3)),
        repeat_mode=payload.get("repeat_mode") or "repeat",
        expire_at=None,
        notify_channel_ids=payload.get("notify_channel_ids") or [],
    )
    db.add(row)
    db.flush()
    return {"rule_id": row.id, "name": row.name}


def _execute_add_position(db: Session, payload: dict) -> dict:
    position = db.query(Position).filter(Position.id == payload["position_id"]).first()
    if not position:
        raise ValueError("持仓不存在")
    add_qty = int(payload["quantity"])
    add_price = float(payload["price"])
    cost_before = float(position.cost_price or 0)
    qty_before = int(position.quantity or 0)

    if _has_today_trade(db, position.id, "buy"):
        raise ValueError("该持仓今日已有买入记录")

    new_qty, new_cost = _calc_weighted_cost(qty_before, cost_before, add_qty, add_price)
    add_amount = round(add_price * add_qty, 4)
    traded_at = datetime.now(timezone.utc).replace(tzinfo=None)

    trade = PositionTrade(
        position_id=position.id,
        side="buy",
        price=add_price,
        quantity=add_qty,
        amount=add_amount,
        cost_before=cost_before,
        qty_before=qty_before,
        cost_after=new_cost,
        qty_after=new_qty,
        note=(payload.get("note") or "").strip() or None,
        traded_at=traded_at,
    )
    db.add(trade)
    position.quantity = new_qty
    position.cost_price = new_cost
    if position.invested_amount is not None:
        position.invested_amount = round(float(position.invested_amount) + add_amount, 4)
    else:
        position.invested_amount = round(new_cost * new_qty, 4)
    db.flush()
    return {
        "position_id": position.id,
        "quantity": new_qty,
        "cost_price": new_cost,
        "trade_id": trade.id,
    }


def _execute_reduce_position(db: Session, payload: dict) -> dict:
    position = db.query(Position).filter(Position.id == payload["position_id"]).first()
    if not position:
        raise ValueError("持仓不存在")
    sell_qty = int(payload["quantity"])
    sell_price = float(payload["price"])
    cost_before = float(position.cost_price or 0)
    qty_before = int(position.quantity or 0)

    if sell_qty > qty_before:
        raise ValueError("卖出股数超过持仓")
    if _has_today_trade(db, position.id, "sell"):
        raise ValueError("该持仓今日已有卖出记录")

    new_qty = qty_before - sell_qty
    sell_amount = round(sell_price * sell_qty, 4)
    traded_at = datetime.now(timezone.utc).replace(tzinfo=None)

    trade = PositionTrade(
        position_id=position.id,
        side="sell",
        price=sell_price,
        quantity=sell_qty,
        amount=sell_amount,
        cost_before=cost_before,
        qty_before=qty_before,
        cost_after=cost_before,
        qty_after=new_qty,
        note=(payload.get("note") or "").strip() or None,
        traded_at=traded_at,
    )
    db.add(trade)
    position.quantity = new_qty
    position.invested_amount = round(cost_before * new_qty, 4)
    db.flush()
    return {
        "position_id": position.id,
        "quantity": new_qty,
        "cost_price": cost_before,
        "trade_id": trade.id,
    }


def execute_pending_action(db: Session, action: ChatPendingAction) -> dict:
    if action.status != "pending":
        raise ValueError(f"操作已{action.status}")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if action.expires_at and action.expires_at < now:
        action.status = "expired"
        raise ValueError("操作已过期，请重新发起")

    permissions = get_chat_action_permissions(db)
    if not permissions.get(action.action_type):
        raise ValueError("当前未开启此操作权限")

    if action.action_type == "create_price_alert":
        result = _execute_create_price_alert(db, action.payload or {})
    elif action.action_type == "add_position":
        result = _execute_add_position(db, action.payload or {})
    elif action.action_type == "reduce_position":
        result = _execute_reduce_position(db, action.payload or {})
    else:
        raise ValueError(f"未知操作类型: {action.action_type}")

    action.status = "confirmed"
    action.result = result
    return result


def cancel_pending_action(db: Session, action: ChatPendingAction) -> None:
    if action.status != "pending":
        raise ValueError(f"操作已{action.status}")
    action.status = "cancelled"


def serialize_pending_action(action: ChatPendingAction) -> dict:
    return {
        "id": action.id,
        "type": action.action_type,
        "preview": action.preview or {},
        "status": action.status,
        "result": action.result,
        "expires_at": str(action.expires_at or ""),
        "created_at": str(action.created_at or ""),
    }


def attach_pending_actions(db: Session, message_ids: list[int]) -> dict[int, list[dict]]:
    if not message_ids:
        return {}
    rows = (
        db.query(ChatPendingAction)
        .filter(ChatPendingAction.message_id.in_(message_ids))
        .order_by(ChatPendingAction.created_at.asc())
        .all()
    )
    out: dict[int, list[dict]] = {}
    for row in rows:
        if row.message_id is None:
            continue
        out.setdefault(row.message_id, []).append(serialize_pending_action(row))
    return out


def link_actions_to_message(db: Session, action_ids: list[str], message_id: int) -> None:
    if not action_ids:
        return
    (
        db.query(ChatPendingAction)
        .filter(ChatPendingAction.id.in_(action_ids))
        .update({ChatPendingAction.message_id: message_id}, synchronize_session=False)
    )
