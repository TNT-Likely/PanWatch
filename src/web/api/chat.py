"""AI 对话 API 端点。"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.config import Settings
from src.core.ai_client import AIClient
from src.core.chat_actions import (
    attach_pending_actions,
    build_action_system_addendum,
    build_action_tools,
    cancel_pending_action,
    execute_pending_action,
    get_chat_action_permissions,
    link_actions_to_message,
    list_notify_channels_tool,
    list_price_alerts_tool,
    propose_create_price_alert,
    propose_add_position,
    propose_reduce_position,
    serialize_pending_action,
)
from src.core.position_trades_context import build_trades_context_text
from src.core.timezone import format_beijing
from src.models.market import MarketCode
from src.web.database import SessionLocal, get_db
from src.web.models import (
    AIModel,
    AIService,
    Account,
    AnalysisHistory,
    ChatConversation,
    ChatMessage,
    ChatPendingAction,
    PaperTradingPosition,
    Position,
    Stock,
    StockSuggestion,
)

logger = logging.getLogger(__name__)
router = APIRouter()

SYSTEM_PROMPT = """你是 PanWatch 的 AI 投资助手。

你可以使用工具获取用户的投资数据。当用户的问题涉及具体数据时，主动调用工具获取，不要让用户自己提供。

规则：
- 需要数据时主动调用工具，不要反问用户要数据
- 基于工具返回的实时数据回答，不编造价格等具体数据
- 给出明确的观点和理由
- 涉及买卖建议时说明风险
- 用中文回答
- 保持简洁，避免冗余
- 给出操作建议前，务必以「当前数据」中的最新持仓股数、成本价和今日已执行买卖为准
- 若用户今日已买入或卖出过，不要重复建议同方向操作；应基于当前剩余仓位和最新成本重新评估
- 「页面快照」仅为对话开始时的参考，与「当前数据」冲突时以「当前数据」为准"""

MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ROUNDS = 5

# ──────────────── Tool Definitions ────────────────

READONLY_CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "获取用户的实盘持仓和模拟盘持仓。用于回答持仓相关问题（持仓健康吗、该调仓吗、盈亏情况等）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": "获取某只股票的实时行情（价格、涨跌幅、成交量等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 600519"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_technical_analysis",
            "description": "获取股票的技术面分析（趋势、MACD、RSI、支撑位、压力位等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_suggestions",
            "description": "获取某只股票最近的 AI 建议和分析报告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_watchlist",
            "description": "获取用户的自选股（关注列表）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_trades",
            "description": "获取用户最近的持仓变动流水（买入/卖出/加仓/减仓），含今日操作。用于判断用户今天是否已买卖过。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "可选，按股票代码筛选"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                    "today_only": {"type": "boolean", "description": "是否只看今日操作", "default": False},
                },
            },
        },
    },
]


def _build_chat_tools(db: Session) -> list[dict]:
    permissions = get_chat_action_permissions(db)
    return READONLY_CHAT_TOOLS + build_action_tools(permissions)


def _build_watchlist_context(db: Session) -> str:
    """构建用户自选股列表。"""
    stocks = db.query(Stock).order_by(
        Stock.is_featured.desc(),
        Stock.sort_order.asc(),
        Stock.id.asc(),
    ).all()
    if not stocks:
        return "用户暂无自选股。"
    lines = [f"- {s.name}({s.market}:{s.symbol})" for s in stocks]
    return "自选股列表：\n" + "\n".join(lines)


async def _execute_tool(
    db: Session,
    name: str,
    args: dict,
    *,
    conversation_id: int,
    pending_action_ids: list[str],
) -> str:
    """执行工具调用，返回结果文本。"""
    try:
        if name == "get_portfolio":
            result = _build_portfolio_context(db)
            return result or "用户暂无持仓。"
        elif name == "get_stock_quote":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = await _fetch_realtime_context(symbol, market)
            return result or f"未能获取 {market}:{symbol} 的行情数据。"
        elif name == "get_technical_analysis":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = await _fetch_technical_context(symbol, market)
            return result or f"未能获取 {market}:{symbol} 的技术面数据。"
        elif name == "get_stock_suggestions":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = _build_stock_context(db, symbol, market)
            return result or f"暂无 {market}:{symbol} 的 AI 建议。"
        elif name == "get_watchlist":
            return _build_watchlist_context(db)
        elif name == "get_recent_trades":
            return build_trades_context_text(
                db,
                symbol=args.get("symbol"),
                market=args.get("market", "CN"),
                today_only=bool(args.get("today_only")),
            ) or "暂无持仓变动流水。"
        elif name == "list_notify_channels":
            return list_notify_channels_tool(db)
        elif name == "list_price_alerts":
            return list_price_alerts_tool(
                db,
                args.get("symbol"),
                args.get("market", "CN"),
            )
        elif name == "propose_create_price_alert":
            result, action_id = propose_create_price_alert(
                db, conversation_id=conversation_id, args=args,
            )
            if action_id:
                pending_action_ids.append(action_id)
            return result
        elif name == "propose_add_position":
            result, action_id = await propose_add_position(
                db, conversation_id=conversation_id, args=args,
            )
            if action_id:
                pending_action_ids.append(action_id)
            return result
        elif name == "propose_reduce_position":
            result, action_id = await propose_reduce_position(
                db, conversation_id=conversation_id, args=args,
            )
            if action_id:
                pending_action_ids.append(action_id)
            return result
        else:
            return f"未知工具: {name}"
    except Exception as e:
        logger.error(f"工具执行失败 {name}: {e}")
        return f"工具执行出错: {e}"


def _serialize_message(db: Session, message: ChatMessage) -> dict:
    pending_map = attach_pending_actions(db, [message.id])
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "created_at": str(message.created_at or ""),
        "pending_actions": pending_map.get(message.id, []),
    }


class CreateConversationBody(BaseModel):
    stock_symbol: str | None = None
    stock_market: str | None = None
    initial_context: str | None = None


class SendMessageBody(BaseModel):
    content: str


def _get_ai_client(db: Session, model_id: int | None = None) -> AIClient:
    """获取 AI 客户端实例。"""
    model = None
    service = None

    if model_id:
        model = db.query(AIModel).filter(AIModel.id == model_id).first()

    if not model:
        model = db.query(AIModel).filter(AIModel.is_default == True).first()  # noqa: E712

    if not model:
        model = db.query(AIModel).first()

    if model:
        service = db.query(AIService).filter(AIService.id == model.service_id).first()

    if model and service:
        return AIClient(
            base_url=service.base_url,
            api_key=service.api_key,
            model=model.model,
        )

    settings = Settings()
    return AIClient(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
    )


def _build_stock_context(db: Session, symbol: str, market: str) -> str:
    """为绑定股票构建上下文摘要。"""
    parts = []

    # 最近建议
    suggestions = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
        )
        .order_by(StockSuggestion.created_at.desc())
        .limit(3)
        .all()
    )
    if suggestions:
        lines = []
        for s in suggestions:
            lines.append(f"- [{s.agent_label or s.agent_name}] {s.action_label}: {s.signal or s.reason or ''}")
        parts.append("最近 AI 建议：\n" + "\n".join(lines))

    # 最近分析报告
    histories = (
        db.query(AnalysisHistory)
        .filter(AnalysisHistory.stock_symbol == symbol)
        .order_by(AnalysisHistory.created_at.desc())
        .limit(1)
        .all()
    )
    if histories:
        h = histories[0]
        content_preview = (h.content or "")[:500]
        parts.append(f"最近分析（{h.agent_name}, {h.analysis_date}）：\n{content_preview}")

    if not parts:
        return ""
    return "\n\n".join(parts)


def _build_recent_trades_context(
    db: Session,
    *,
    symbol: str | None = None,
    market: str | None = None,
    today_only: bool = False,
    limit: int = 20,
) -> str:
    """构建最近持仓变动流水摘要。"""
    return build_trades_context_text(
        db,
        symbol=symbol,
        market=market,
        today_only=today_only,
        limit=limit,
    )


def _build_stock_position_context(db: Session, symbol: str, market: str) -> str:
    """构建单只股票的详细持仓摘要（含各账户明细）。"""
    positions = (
        db.query(Position)
        .join(Stock, Position.stock_id == Stock.id)
        .filter(Stock.symbol == symbol, Stock.market == market)
        .all()
    )
    if not positions:
        return f"当前未持有 {market}:{symbol}。"

    lines: list[str] = []
    total_qty = 0
    total_cost_value = 0.0
    for p in positions:
        stock = p.stock
        if not stock:
            continue
        qty = int(p.quantity or 0)
        cost = float(p.cost_price or 0)
        total_qty += qty
        total_cost_value += qty * cost
        lines.append(
            f"- {p.account.name if p.account else '账户'}: "
            f"{qty}股 成本单价{cost:.4f} 风格{p.trading_style or '短线'}"
        )

    unit_cost = total_cost_value / total_qty if total_qty > 0 else 0.0
    header = (
        f"标的持仓汇总：{market}:{symbol} 合计{total_qty}股 "
        f"加权成本{unit_cost:.4f}"
    )
    return header + "\n" + "\n".join(lines)


def _build_portfolio_context(db: Session) -> str:
    """构建用户全部持仓摘要。"""
    lines: list[str] = []

    # 实盘持仓
    positions = db.query(Position).all()
    if positions:
        real_lines = []
        for p in positions:
            stock = db.query(Stock).filter(Stock.id == p.stock_id).first()
            if not stock:
                continue
            real_lines.append(
                f"- {stock.name}({stock.market}:{stock.symbol}) "
                f"{p.quantity}股 成本单价{p.cost_price:.4f} "
                f"风格{p.trading_style or '短线'}"
            )
        if real_lines:
            lines.append("实盘持仓（最新）：\n" + "\n".join(real_lines))

    # 模拟盘持仓
    paper_positions = (
        db.query(PaperTradingPosition)
        .filter(PaperTradingPosition.status == "open")
        .all()
    )
    if paper_positions:
        paper_lines = []
        for pp in paper_positions:
            pnl_str = f"浮盈{pp.unrealized_pnl:.1f}" if pp.unrealized_pnl else ""
            paper_lines.append(
                f"- {pp.stock_name or pp.stock_symbol}({pp.stock_market}:{pp.stock_symbol}) "
                f"{pp.quantity}股 入场价{pp.entry_price}"
                f"{f' 止损{pp.stop_loss}' if pp.stop_loss else ''}"
                f"{f' 目标{pp.target_price}' if pp.target_price else ''}"
                f"{f' {pnl_str}' if pnl_str else ''}"
            )
        if paper_lines:
            lines.append("模拟盘持仓：\n" + "\n".join(paper_lines))

    if not lines:
        return ""
    return "\n\n".join(lines)


async def _fetch_realtime_context(symbol: str, market: str) -> str:
    """异步获取实时行情和技术面。"""
    try:
        from src.collectors.akshare_collector import _fetch_tencent_quotes, _tencent_symbol
        from src.models.market import MarketCode

        mc = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
        tsym = _tencent_symbol(symbol, mc)
        rows = await asyncio.to_thread(_fetch_tencent_quotes, [tsym])
        if not rows:
            return ""
        q = rows[0]
        price = q.get("current_price", "--")
        change = q.get("change_pct", "--")
        volume = q.get("volume", "--")
        name = q.get("name", symbol)
        return f"实时行情：{name}（{market}:{symbol}）价格 {price}，涨跌幅 {change}%，成交量 {volume}"
    except Exception as e:
        logger.debug(f"获取实时行情失败: {e}")
        return ""


async def _fetch_technical_context(symbol: str, market: str) -> str:
    """获取技术面摘要。"""
    try:
        from src.core.data_collector import DataCollector

        collector = DataCollector()
        summary = await asyncio.to_thread(
            collector.get_kline_summary, symbol, market
        )
        if not summary or summary.get("error"):
            return ""
        s = summary.get("summary", {})
        trend = s.get("trend", "--")
        macd = s.get("macd_status", "--")
        rsi = s.get("rsi_14", "--")
        support = s.get("support_level", "--")
        resistance = s.get("resistance_level", "--")
        return f"技术面：趋势 {trend}，MACD {macd}，RSI {rsi}，支撑位 {support}，压力位 {resistance}"
    except Exception as e:
        logger.debug(f"获取技术面失败: {e}")
        return ""


@router.get("/suggested-questions")
def suggested_questions(
    symbol: str = Query(..., description="股票代码"),
    market: str = Query("CN", description="市场"),
    db: Session = Depends(get_db),
):
    """根据股票当前状态生成推荐问题（纯模板，不调 AI）。"""
    questions: list[str] = []

    # 查最近建议
    latest_suggestion = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
        )
        .order_by(StockSuggestion.created_at.desc())
        .first()
    )
    if latest_suggestion:
        action = (latest_suggestion.action or "").lower()
        label = latest_suggestion.action_label or latest_suggestion.action or ""
        if action in ("buy", "add"):
            questions.append(f"最新的「{label}」信号可靠吗？入场时机如何？")
        elif action in ("sell", "reduce"):
            questions.append(f"最新给出了「{label}」建议，现在该操作吗？")
        elif action == "alert":
            questions.append("最近的异动提醒是什么情况？需要关注吗？")

    # 查持仓（Position 通过 stock_id 关联 Stock 表）
    has_position = (
        db.query(Position)
        .join(Stock, Position.stock_id == Stock.id)
        .filter(Stock.symbol == symbol, Stock.market == market)
        .first()
    ) is not None
    if has_position:
        questions.append("当前持仓该继续持有还是考虑减仓？")
    else:
        questions.append("现在适合建仓吗？")

    # 通用问题
    questions.append("分析近期走势和关键支撑压力位")
    questions.append("有什么值得关注的消息或事件？")

    return {"questions": questions[:5]}


@router.post("/conversations")
def create_conversation(
    body: CreateConversationBody | None = None,
    db: Session = Depends(get_db),
):
    conv = ChatConversation(
        stock_symbol=body.stock_symbol if body else None,
        stock_market=body.stock_market if body else None,
        initial_context=body.initial_context if body else None,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {
        "id": conv.id,
        "title": conv.title or "",
        "stock_symbol": conv.stock_symbol,
        "stock_market": conv.stock_market,
        "created_at": str(conv.created_at or ""),
    }


def _serialize_conversation(conv: ChatConversation) -> dict:
    return {
        "id": conv.id,
        "title": conv.title or "",
        "stock_symbol": conv.stock_symbol,
        "stock_market": conv.stock_market,
        "created_at": str(conv.created_at or ""),
        "updated_at": str(conv.updated_at or conv.created_at or ""),
    }


@router.get("/conversations")
def list_conversations(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ChatConversation)
        .order_by(ChatConversation.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_conversation(c) for c in rows]


@router.get("/conversations/recent")
def list_recent_conversations(
    symbol: str = Query(..., min_length=1),
    market: str = Query(..., min_length=1),
    limit: int = Query(1, ge=1, le=10),
    db: Session = Depends(get_db),
):
    """按股票查询最近活跃对话（须在 /conversations/{id} 之前注册）。"""
    rows = (
        db.query(ChatConversation)
        .filter(
            ChatConversation.stock_symbol == symbol,
            ChatConversation.stock_market == market,
        )
        .order_by(ChatConversation.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_conversation(c) for c in rows]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "对话不存在")
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    pending_map = attach_pending_actions(db, [m.id for m in messages])
    return {
        "conversation": {
            "id": conv.id,
            "title": conv.title or "",
            "stock_symbol": conv.stock_symbol,
            "stock_market": conv.stock_market,
            "created_at": str(conv.created_at or ""),
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": str(m.created_at or ""),
                "pending_actions": pending_map.get(m.id, []),
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "对话不存在")
    db.query(ChatPendingAction).filter(ChatPendingAction.conversation_id == conversation_id).delete()
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).delete()
    db.delete(conv)
    db.commit()
    return {"ok": True}


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: SendMessageBody,
):
    """发送消息并获取 AI 回复。"""
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            raise HTTPException(404, "对话不存在")

        # 保存用户消息
        user_msg = ChatMessage(
            conversation_id=conversation_id,
            role="user",
            content=body.content,
        )
        db.add(user_msg)

        # 更新对话标题（首条消息取前 20 字）
        if not conv.title:
            conv.title = body.content[:20]

        db.commit()
        db.refresh(user_msg)

        # 构建消息列表
        messages_for_ai: list[dict] = []

        # System prompt
        system_content = SYSTEM_PROMPT
        permissions = get_chat_action_permissions(db)
        system_content += build_action_system_addendum(permissions)

        # 绑定股票提示
        if conv.stock_symbol and conv.stock_market:
            system_content += f"\n\n当前对话关联股票：{conv.stock_market}:{conv.stock_symbol}"

        # 前端页面快照（对话创建时传入，可能已过时）
        if conv.initial_context:
            created_hint = ""
            if conv.created_at:
                created_hint = f"（创建于 {format_beijing(conv.created_at, '%Y-%m-%d %H:%M')}，仅供参考）"
            system_content += (
                f"\n\n--- 用户页面快照{created_hint} ---\n"
                + conv.initial_context
                + "\n注意：若与下方「当前数据」冲突，以「当前数据」为准。"
            )

        messages_for_ai.append({"role": "system", "content": system_content})

        # 历史消息
        history = (
            db.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        recent = history[-MAX_HISTORY_MESSAGES:] if len(history) > MAX_HISTORY_MESSAGES else history
        for m in recent:
            if m.role in ("user", "assistant"):
                messages_for_ai.append({"role": m.role, "content": m.content})

        # 注入基础上下文（持仓 + 绑定股票的行情/建议）
        context_parts: list[str] = []

        # 用户持仓
        portfolio_ctx = _build_portfolio_context(db)
        if portfolio_ctx:
            context_parts.append(portfolio_ctx)

        # 今日及近期持仓变动
        today_trades = _build_recent_trades_context(
            db,
            symbol=conv.stock_symbol,
            market=conv.stock_market,
            today_only=True,
            limit=10,
        )
        if today_trades:
            context_parts.append(today_trades)
        elif conv.stock_symbol and conv.stock_market:
            stock_trades = _build_recent_trades_context(
                db,
                symbol=conv.stock_symbol,
                market=conv.stock_market,
                today_only=False,
                limit=8,
            )
            if stock_trades:
                context_parts.append(stock_trades)
        else:
            recent_trades = _build_recent_trades_context(db, today_only=False, limit=8)
            if recent_trades:
                context_parts.append(recent_trades)

        # 绑定股票的持仓明细
        if conv.stock_symbol and conv.stock_market:
            stock_pos = _build_stock_position_context(db, conv.stock_symbol, conv.stock_market)
            if stock_pos:
                context_parts.append(stock_pos)

        # 绑定股票的实时数据
        if conv.stock_symbol and conv.stock_market:
            realtime = await _fetch_realtime_context(conv.stock_symbol, conv.stock_market)
            if realtime:
                context_parts.append(realtime)
            technical = await _fetch_technical_context(conv.stock_symbol, conv.stock_market)
            if technical:
                context_parts.append(technical)
            stock_ctx = _build_stock_context(db, conv.stock_symbol, conv.stock_market)
            if stock_ctx:
                context_parts.append(stock_ctx)

        if context_parts:
            # 把上下文追加到 system message
            messages_for_ai[0]["content"] += "\n\n--- 当前数据 ---\n" + "\n\n".join(context_parts)

        # 调用 AI（带 tool use，用于按需获取更多数据）
        ai_client = _get_ai_client(db, conv.ai_model_id)
        chat_tools = _build_chat_tools(db)
        ai_response = ""
        pending_action_ids: list[str] = []
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                try:
                    response_msg = await ai_client.chat_with_tools(
                        messages_for_ai, tools=chat_tools, temperature=0.5,
                    )
                except Exception:
                    # 模型不支持 tool use → 直接用 chat_multi
                    logger.info("Tool use 不可用，使用普通对话")
                    ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                    break

                if not response_msg.tool_calls:
                    ai_response = response_msg.content or ""
                    break

                # 执行 tool calls
                messages_for_ai.append({
                    "role": "assistant",
                    "content": response_msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in response_msg.tool_calls
                    ],
                })

                for tc in response_msg.tool_calls:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    logger.info(f"Tool call: {tc.function.name}({tool_args})")
                    result = await _execute_tool(
                        db,
                        tc.function.name,
                        tool_args,
                        conversation_id=conversation_id,
                        pending_action_ids=pending_action_ids,
                    )
                    messages_for_ai.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
            else:
                ai_response = response_msg.content or "抱歉，处理轮次过多，请精简问题再试。"

        except Exception as e:
            logger.error(f"AI 对话失败: {e}")
            ai_response = f"抱歉，AI 服务暂时不可用：{e}"

        # 保存 AI 回复
        assistant_msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
        )
        db.add(assistant_msg)
        db.flush()

        if pending_action_ids:
            link_actions_to_message(db, pending_action_ids, assistant_msg.id)

        # 更新对话时间
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)

        return _serialize_message(db, assistant_msg)
    finally:
        db.close()


@router.post("/actions/{action_id}/confirm")
def confirm_action(action_id: str, db: Session = Depends(get_db)):
    """确认并执行 AI 对话中的待确认操作。"""
    action = db.query(ChatPendingAction).filter(ChatPendingAction.id == action_id).first()
    if not action:
        raise HTTPException(404, "操作不存在")
    try:
        result = execute_pending_action(db, action)
        db.commit()
        db.refresh(action)
    except ValueError as e:
        db.commit()
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        db.rollback()
        logger.error("确认操作失败 %s: %s", action_id, e)
        raise HTTPException(500, f"执行失败: {e}") from e

    return {
        "ok": True,
        "action": serialize_pending_action(action),
        "result": result,
    }


@router.post("/actions/{action_id}/cancel")
def cancel_action(action_id: str, db: Session = Depends(get_db)):
    """取消 AI 对话中的待确认操作。"""
    action = db.query(ChatPendingAction).filter(ChatPendingAction.id == action_id).first()
    if not action:
        raise HTTPException(404, "操作不存在")
    try:
        cancel_pending_action(db, action)
        db.commit()
        db.refresh(action)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    return {"ok": True, "action": serialize_pending_action(action)}
