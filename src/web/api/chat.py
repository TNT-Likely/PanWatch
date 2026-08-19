"""AI 对话 API 端点。"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.config import Settings
from src.core.ai_failover import FailoverAIClient, build_failover_client
from src.core.sse import SSEStream, chat_stream_hub
from src.models.market import MarketCode
from src.web.database import SessionLocal, get_db
from src.web.models import (
    AIModel,
    AIService,
    AnalysisHistory,
    ChatConversation,
    ChatMessage,
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
- 保持简洁，避免冗余"""

MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ROUNDS = 5

# ──────────────── Tool Definitions ────────────────

CHAT_TOOLS = [
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
]


def _build_watchlist_context(db: Session) -> str:
    """构建用户自选股列表。"""
    stocks = db.query(Stock).order_by(Stock.sort_order.asc()).all()
    if not stocks:
        return "用户暂无自选股。"
    lines = [f"- {s.name}({s.market}:{s.symbol})" for s in stocks]
    return "自选股列表：\n" + "\n".join(lines)


async def _execute_tool(db: Session, name: str, args: dict) -> str:
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
        else:
            return f"未知工具: {name}"
    except Exception as e:
        logger.error(f"工具执行失败 {name}: {e}")
        return f"工具执行出错: {e}"


class CreateConversationBody(BaseModel):
    stock_symbol: str | None = None
    stock_market: str | None = None
    initial_context: str | None = None


class SendMessageBody(BaseModel):
    content: str


def _get_ai_client(db: Session, model_id: int | None = None) -> FailoverAIClient:
    """获取带 failover 的 AI 客户端（主模型沿用三级选取，备选从库里补齐）。

    对话工具循环 / 组合体检等直接调用方都经此入口 —— 主模型超时/限流/挂掉时
    自动降级备选，实际使用的模型记在 used_model_label，可回填到 done 事件供前端
    透明展示。返回的 FailoverAIClient 与 AIClient 接口兼容，可原地替换。
    """
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
    return build_failover_client(model, service, db=db)


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
                f"{p.quantity}股 成本{p.cost_price} 风格{p.trading_style or '波段'}"
            )
        if real_lines:
            lines.append("实盘持仓：\n" + "\n".join(real_lines))

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
        from src.core.marketdata_client import md_quote_rows
        from src.models.market import MarketCode

        mc = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
        rows = await asyncio.to_thread(md_quote_rows, [symbol], mc.value)
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
    return [
        {
            "id": c.id,
            "title": c.title or "",
            "stock_symbol": c.stock_symbol,
            "stock_market": c.stock_market,
            "created_at": str(c.created_at or ""),
        }
        for c in rows
    ]


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
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "对话不存在")
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).delete()
    db.delete(conv)
    db.commit()
    return {"ok": True}


def _save_user_message(db: Session, conv: ChatConversation, content: str) -> ChatMessage:
    """保存用户消息并按需生成对话标题（流式/非流式共用）。"""
    user_msg = ChatMessage(
        conversation_id=conv.id,
        role="user",
        content=content,
    )
    db.add(user_msg)

    # 更新对话标题（首条消息取前 20 字）
    if not conv.title:
        conv.title = content[:20]

    db.commit()
    db.refresh(user_msg)
    return user_msg


async def _build_messages_for_ai(db: Session, conv: ChatConversation) -> list[dict]:
    """构建发给模型的完整 messages（system prompt + 历史 + 数据上下文，流式/非流式共用）。"""
    messages_for_ai: list[dict] = []

    # System prompt
    system_content = SYSTEM_PROMPT

    # 绑定股票提示
    if conv.stock_symbol and conv.stock_market:
        system_content += f"\n\n当前对话关联股票：{conv.stock_market}:{conv.stock_symbol}"

    # 前端页面快照（对话创建时传入）
    if conv.initial_context:
        system_content += "\n\n--- 用户页面快照（对话创建时） ---\n" + conv.initial_context

    messages_for_ai.append({"role": "system", "content": system_content})

    # 历史消息
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv.id)
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

    return messages_for_ai


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: SendMessageBody,
):
    """发送消息并获取 AI 回复（非流式，保留作兼容与降级兜底）。"""
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            raise HTTPException(404, "对话不存在")

        _save_user_message(db, conv, body.content)
        messages_for_ai = await _build_messages_for_ai(db, conv)

        # 调用 AI（带 tool use，用于按需获取更多数据；主模型失败自动 failover）
        ai_client = _get_ai_client(db, conv.ai_model_id)
        ai_response = ""
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                try:
                    response_msg = await ai_client.chat_with_tools(
                        messages_for_ai, tools=CHAT_TOOLS, temperature=0.5,
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
                    result = await _execute_tool(db, tc.function.name, tool_args)
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

        # 更新对话时间
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)

        return {
            "id": assistant_msg.id,
            "role": "assistant",
            "content": assistant_msg.content,
            "created_at": str(assistant_msg.created_at or ""),
        }
    finally:
        db.close()


# ──────────────── SSE 流式对话 ────────────────
#
# 事件分型（均带自增 id，供 Last-Event-ID 续推）：
# - meta:            {stream_id, conversation_id, user_message_id} 首条，供断线重连定位流
# - token:           {text} 增量文本；工具调用轮的过渡性文本也会流出，前端在收到
#                    tool_call_start 时应清空当前缓冲（最终落库的只有末轮回答）
# - tool_call_start: {name, arguments} 模型决定调用工具（前端可视化"正在查询…"）
# - tool_result:     {name, ok, preview} 工具执行完成（preview 截断，完整结果只进模型上下文）
# - done:            {message_id, content, created_at} 最终回答（已落库）
# - error:           {message} AI 服务异常（错误文案同样落库，行为与非流式端点一致）
#
# 生成任务与 SSE 连接解耦：任务往 SSEStream 缓冲推事件，连接断开不影响生成与落库；
# 前端可用 GET /chat/streams/{stream_id} + Last-Event-ID 续推。

TOOL_RESULT_PREVIEW_CHARS = 200


async def _run_chat_stream_task(conversation_id: int, stream: SSEStream) -> None:
    """后台执行对话生成（工具循环 + token 流），事件推入 stream。"""
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            await stream.publish("error", {"message": "对话不存在"})
            return

        messages_for_ai = await _build_messages_for_ai(db, conv)
        ai_client = _get_ai_client(db, conv.ai_model_id)
        ai_response = ""

        try:
            final_msg: dict | None = None
            for _round in range(MAX_TOOL_ROUNDS):
                final_msg = None
                try:
                    async for kind, payload in ai_client.chat_stream(
                        messages_for_ai, tools=CHAT_TOOLS, temperature=0.5,
                    ):
                        if kind == "token":
                            await stream.publish("token", {"text": payload})
                        else:
                            final_msg = payload
                except Exception:
                    # 模型不支持 tool use / 流式 → 降级为普通对话（与非流式端点同策略）
                    logger.info("流式 tool use 不可用，降级为普通对话")
                    ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                    await stream.publish("token", {"text": ai_response})
                    break

                tool_calls = (final_msg or {}).get("tool_calls") or []
                if not tool_calls:
                    ai_response = (final_msg or {}).get("content") or ""
                    break

                # 有工具调用：把 assistant 消息 + 工具结果追加进上下文，进入下一轮
                messages_for_ai.append({
                    "role": "assistant",
                    "content": (final_msg or {}).get("content") or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["arguments"]},
                        }
                        for tc in tool_calls
                    ],
                })
                for tc in tool_calls:
                    try:
                        tool_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        tool_args = {}
                    logger.info(f"Tool call(stream): {tc['name']}({tool_args})")
                    await stream.publish(
                        "tool_call_start", {"name": tc["name"], "arguments": tool_args}
                    )
                    result = await _execute_tool(db, tc["name"], tool_args)
                    await stream.publish(
                        "tool_result",
                        {
                            "name": tc["name"],
                            "ok": not result.startswith("工具执行出错"),
                            "preview": (result or "")[:TOOL_RESULT_PREVIEW_CHARS],
                        },
                    )
                    messages_for_ai.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
            else:
                ai_response = (final_msg or {}).get("content") or "抱歉，处理轮次过多，请精简问题再试。"

        except Exception as e:
            logger.error(f"AI 流式对话失败: {e}")
            ai_response = f"抱歉，AI 服务暂时不可用：{e}"
            await stream.publish("error", {"message": str(e)})

        # 落库（无论连接是否还在，结果照常持久化）
        assistant_msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
        )
        db.add(assistant_msg)
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)

        await stream.publish("done", {
            "message_id": assistant_msg.id,
            "content": ai_response,
            "created_at": str(assistant_msg.created_at or ""),
            # 实际使用的模型标签(failover 后可能非主模型),供前端透明展示
            "model_label": getattr(ai_client, "used_model_label", ""),
        })
    except Exception as e:
        logger.error(f"对话流式任务异常: {e}")
        try:
            await stream.publish("error", {"message": str(e)})
        except Exception:
            pass
    finally:
        await stream.finish()
        db.close()


def _sse_response(stream: SSEStream, after_seq: int = 0) -> StreamingResponse:
    """把 SSEStream 包成 text/event-stream 响应（响应包装中间件对该类型直通）。"""
    return StreamingResponse(
        stream.subscribe(after_seq=after_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 禁用 nginx 等反代的缓冲，保证事件实时下发
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: int,
    body: SendMessageBody,
):
    """发送消息并以 SSE 流式返回 AI 回复（token 流 + 工具过程可视）。

    非流式端点 POST /messages 保留不动，前端在流式失败时降级使用。
    """
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            raise HTTPException(404, "对话不存在")
        user_msg = _save_user_message(db, conv, body.content)
        user_message_id = user_msg.id
    finally:
        db.close()

    stream = chat_stream_hub.create()
    # meta 事件放最前：告知 stream_id，断线后可 GET /chat/streams/{stream_id} 续推
    await stream.publish("meta", {
        "stream_id": stream.stream_id,
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
    })
    # 生成任务独立运行，不随本次响应连接断开而中止
    asyncio.create_task(_run_chat_stream_task(conversation_id, stream))
    return _sse_response(stream)


@router.get("/streams/{stream_id}")
async def resume_message_stream(
    stream_id: str,
    request: Request,
    last_event_id: int = Query(0, ge=0, description="断线前收到的最后事件序号"),
):
    """断线重连：按 Last-Event-ID（header 优先，query 兜底）从缓冲续推。"""
    stream = chat_stream_hub.get(stream_id)
    if not stream:
        raise HTTPException(404, "流不存在或已过期")
    header_id = request.headers.get("last-event-id", "")
    after_seq = int(header_id) if header_id.isdigit() else last_event_id
    return _sse_response(stream, after_seq=after_seq)
