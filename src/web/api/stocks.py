import asyncio
import logging
import threading
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import (
    Stock,
    StockAgent,
    AgentConfig,
    Position,
    PriceAlertRule,
    PriceAlertHit,
)
from src.config import _infer_security_type as _infer_security_type_for_symbol
from src.core.stock_concept_tags import (
    merge_concept_tags,
    normalize_manual_tags,
    refresh_stock_concept_tags,
    schedule_refresh_missing_concept_tags,
    schedule_refresh_stock_concept_tags,
)
from src.core.long_term_plan import (
    evaluate_add_plan,
    normalize_investment_profile,
    portfolio_role_label,
)
from src.collectors.akshare_collector import _tencent_symbol, _fetch_tencent_quotes
from src.models.market import MarketCode, MARKETS
from src.core.agent_catalog import AGENT_KIND_WORKFLOW, infer_agent_kind
from src.web.stock_list import search_stocks, refresh_stock_list

logger = logging.getLogger(__name__)
router = APIRouter()


class StockCreate(BaseModel):
    symbol: str
    name: str
    market: str = "CN"
    security_type: str = "stock"  # stock / etf / index


class StockUpdate(BaseModel):
    name: str | None = None


class StockAgentInfo(BaseModel):
    agent_name: str
    schedule: str = ""
    ai_model_id: int | None = None
    notify_channel_ids: list[int] = []


class StockConceptTag(BaseModel):
    name: str
    source: str  # auto / manual


class StockResponse(BaseModel):
    id: int
    symbol: str
    name: str
    market: str
    security_type: str = "stock"
    sort_order: int
    concept_tags: list[StockConceptTag] = []
    concept_tags_auto: list[str] = []
    concept_tags_manual: list[str] = []
    investment_profile: dict = {}
    agents: list[StockAgentInfo] = []

    class Config:
        from_attributes = True


class InvestmentProfileUpdate(BaseModel):
    long_term_enabled: bool | None = None
    portfolio_role: str | None = None  # core / satellite / watch
    target_weight_pct: float | None = None
    max_weight_pct: float | None = None
    add_plan: dict | None = None
    reduce_plan: dict | None = None
    thesis: str | None = None
    thesis_invalidations: list[str] | None = None


class StockConceptTagsUpdate(BaseModel):
    manual: list[str]


class StockConceptTagsRefreshRequest(BaseModel):
    limit: int = 20


class StockAgentItem(BaseModel):
    agent_name: str
    schedule: str = ""
    ai_model_id: int | None = None
    notify_channel_ids: list[int] = []


class StockAgentUpdate(BaseModel):
    agents: list[StockAgentItem]


class StockReorderItem(BaseModel):
    id: int
    sort_order: int


class StockReorderRequest(BaseModel):
    items: list[StockReorderItem]


def _stock_to_response(stock: Stock) -> dict:
    profile = normalize_investment_profile(stock.investment_profile)
    return {
        "id": stock.id,
        "symbol": stock.symbol,
        "name": stock.name,
        "market": stock.market,
        "security_type": stock.security_type or "stock",
        "sort_order": stock.sort_order or 0,
        "concept_tags": merge_concept_tags(stock),
        "concept_tags_auto": stock.concept_tags_auto or [],
        "concept_tags_manual": stock.concept_tags_manual or [],
        "investment_profile": profile,
        "agents": [
            {
                "agent_name": sa.agent_name,
                "schedule": sa.schedule or "",
                "ai_model_id": sa.ai_model_id,
                "notify_channel_ids": sa.notify_channel_ids or [],
            }
            for sa in stock.agents
            if infer_agent_kind(sa.agent_name) == AGENT_KIND_WORKFLOW
        ],
    }


@router.get("/markets/status")
def get_market_status():
    """获取各市场的交易状态"""
    from datetime import datetime

    result = []
    for market_code, market_def in MARKETS.items():
        try:
            now = datetime.now(market_def.get_tz())
            is_trading = market_def.is_trading_time()

            # 获取交易时段描述
            sessions_desc = []
            for session in market_def.sessions:
                sessions_desc.append(f"{session.start.strftime('%H:%M')}-{session.end.strftime('%H:%M')}")

            # 判断状态
            weekday = now.weekday()
            current_time = now.time()

            if weekday >= 5:
                status = "closed"
                status_text = "休市（周末）"
            elif is_trading:
                status = "trading"
                status_text = "交易中"
            else:
                # 判断是盘前还是盘后
                first_session = market_def.sessions[0]
                last_session = market_def.sessions[-1]
                if current_time < first_session.start:
                    status = "pre_market"
                    status_text = "盘前"
                elif current_time > last_session.end:
                    status = "after_hours"
                    status_text = "已收盘"
                else:
                    status = "break"
                    status_text = "午间休市"

            result.append({
                "code": market_code.value,
                "name": market_def.name,
                "status": status,
                "status_text": status_text,
                "is_trading": is_trading,
                "sessions": sessions_desc,
                "local_time": now.strftime("%H:%M"),
                "timezone": market_def.timezone,
            })
        except Exception as e:
            # 单个市场获取失败不影响其他市场
            logger.error(f"获取 {market_code.value} 市场状态失败: {e}")
            result.append({
                "code": market_code.value,
                "name": market_def.name,
                "status": "unknown",
                "status_text": "未知",
                "is_trading": False,
                "sessions": [],
                "local_time": "--:--",
                "timezone": market_def.timezone,
                "error": str(e),
            })

    return result


@router.get("/search")
def search(q: str = Query("", min_length=1), market: str = Query("")):
    """模糊搜索股票(代码/名称)"""
    return search_stocks(q, market)


@router.get("/etf/{code}/overview")
def etf_overview(code: str, top: int = Query(30, ge=1, le=100), nav_days: int = Query(180, ge=7, le=1095)):
    """场内 ETF 详情:实时行情(IOPV/折价率/规模) + 成分股 + 净值历史。

    数据来自 akshare(东财),spot 全量缓存 15min,成分股/净值缓存 1h。
    各部分独立兜底,单只 ETF 缺数据不影响其余字段。
    """
    from src.collectors.etf_collector import get_etf_overview

    code = (code or "").strip()
    if not code:
        raise HTTPException(400, "ETF 代码不能为空")
    return get_etf_overview(code, top=top, nav_days=nav_days)


@router.post("/refresh-list")
def refresh_list():
    """刷新股票列表缓存"""
    stocks = refresh_stock_list()
    return {"count": len(stocks)}


@router.get("", response_model=list[StockResponse])
def list_stocks(db: Session = Depends(get_db)):
    stocks = db.query(Stock).order_by(Stock.sort_order.asc(), Stock.id.asc()).all()
    if any(
        (s.market or "").upper() == "CN" and not (s.concept_tags_auto or [])
        for s in stocks
    ):
        schedule_refresh_missing_concept_tags()
    return [_stock_to_response(s) for s in stocks]


@router.get("/quotes")
def get_quotes(db: Session = Depends(get_db)):
    """获取所有自选股的实时行情"""
    stocks = db.query(Stock).all()
    if not stocks:
        return {}

    # 按市场分组
    market_stocks: dict[str, list[Stock]] = {}
    for s in stocks:
        market_stocks.setdefault(s.market, []).append(s)

    quotes = {}
    for market, stock_list in market_stocks.items():
        try:
            market_code = MarketCode(market)
        except ValueError:
            continue

        symbols = [_tencent_symbol(s.symbol, market_code) for s in stock_list]
        try:
            items = _fetch_tencent_quotes(symbols)
            for item in items:
                quotes[item["symbol"]] = {
                    "current_price": item["current_price"],
                    "change_pct": item["change_pct"],
                    "change_amount": item["change_amount"],
                    "prev_close": item["prev_close"],
                }
        except Exception as e:
            logger.error(f"获取 {market} 行情失败: {e}")

    return quotes


@router.post("", response_model=StockResponse)
def create_stock(stock: StockCreate, db: Session = Depends(get_db)):
    existing = db.query(Stock).filter(
        Stock.symbol == stock.symbol, Stock.market == stock.market
    ).first()
    if existing:
        raise HTTPException(400, f"股票 {stock.symbol} 已存在")

    max_order = db.query(func.max(Stock.sort_order)).scalar() or 0
    db_stock = Stock(**stock.model_dump(), sort_order=int(max_order) + 1)
    db.add(db_stock)
    db.commit()
    db.refresh(db_stock)
    schedule_refresh_stock_concept_tags(db_stock.id)
    return _stock_to_response(db_stock)


@router.put("/reorder")
def reorder_stocks(body: StockReorderRequest, db: Session = Depends(get_db)):
    if not body.items:
        return {"updated": 0}
    ids = [int(x.id) for x in body.items]
    rows = db.query(Stock).filter(Stock.id.in_(ids)).all()
    row_map = {r.id: r for r in rows}
    updated = 0
    for item in body.items:
        row = row_map.get(int(item.id))
        if not row:
            continue
        row.sort_order = int(item.sort_order)
        updated += 1
    db.commit()
    return {"updated": updated}


@router.post("/concept-tags/refresh")
def refresh_concept_tags_batch(
    body: StockConceptTagsRefreshRequest | None = None,
):
    """后台刷新尚未拉取概念标签的 A 股。"""
    limit = max(1, min(int((body.limit if body else 20)), 50))
    schedule_refresh_missing_concept_tags(limit=limit)
    return {"queued": True, "limit": limit}


@router.put("/{stock_id}/concept-tags", response_model=StockResponse)
def update_stock_concept_tags(
    stock_id: int,
    body: StockConceptTagsUpdate,
    db: Session = Depends(get_db),
):
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    db_stock.concept_tags_manual = normalize_manual_tags(body.manual)
    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock)


@router.post("/{stock_id}/concept-tags/refresh", response_model=StockResponse)
def refresh_stock_concept_tags_api(stock_id: int, db: Session = Depends(get_db)):
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")
    if (db_stock.market or "").upper() != "CN":
        raise HTTPException(400, "仅 A 股支持自动拉取概念标签")

    try:
        refresh_stock_concept_tags(db, db_stock)
    except Exception as e:
        logger.error("刷新概念标签失败 %s: %s", db_stock.symbol, e)
        raise HTTPException(503, "概念标签数据源暂不可用，请稍后重试")
    return _stock_to_response(db_stock)


def _portfolio_snapshot_for_stock(db: Session, stock: Stock) -> dict:
    """汇总该股票相关持仓与账户资金，供加仓计划评估。"""
    from src.web.models import Account
    from src.web.api.accounts import (
        _fetch_quotes_for_stocks,
        get_hkd_cny_rate,
        get_usd_cny_rate,
    )

    enabled_accounts = db.query(Account).filter(Account.enabled == True).all()  # noqa: E712
    available_cash = sum(float(acc.available_funds or 0) for acc in enabled_accounts)

    all_positions = (
        db.query(Position)
        .join(Account, Position.account_id == Account.id)
        .filter(Account.enabled == True)  # noqa: E712
        .all()
    )
    open_positions = [p for p in all_positions if (p.status or "open") == "open"]

    stock_ids = {p.stock_id for p in open_positions}
    stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all() if stock_ids else []
    stock_map = {s.id: s for s in stocks}
    quotes = _fetch_quotes_for_stocks(stocks)
    hkd_rate = get_hkd_cny_rate()
    usd_rate = get_usd_cny_rate()

    total_market_value = 0.0
    position_market_value = 0.0
    total_qty = 0
    total_cost_value = 0.0

    for pos in open_positions:
        s = stock_map.get(pos.stock_id)
        if not s:
            continue
        qty = int(pos.quantity or 0)
        cost = float(pos.cost_price or 0)
        rate = hkd_rate if s.market == "HK" else usd_rate if s.market == "US" else 1.0

        quote = quotes.get(s.symbol)
        price = quote["current_price"] if quote and quote.get("current_price") else cost
        mv_cny = float(price) * qty * rate
        total_market_value += mv_cny

        if pos.stock_id == stock.id:
            total_qty += qty
            total_cost_value += qty * cost
            position_market_value += mv_cny

    total_assets = total_market_value + available_cash
    avg_cost = total_cost_value / total_qty if total_qty > 0 else None

    from src.core.position_trades_context import summarize_today_trades

    today = summarize_today_trades(db, symbol=stock.symbol, market=stock.market)

    return {
        "avg_cost": avg_cost,
        "position_value": round(position_market_value, 2),
        "total_assets": round(total_assets, 2),
        "total_market_value": round(total_market_value, 2),
        "available_cash": round(available_cash, 2),
        "has_buy_today": today.get("has_buy_today", False),
    }


@router.get("/{stock_id}/investment-profile")
def get_investment_profile(stock_id: int, db: Session = Depends(get_db)):
    """获取股票长线投资计划。"""
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")
    profile = normalize_investment_profile(db_stock.investment_profile)
    return {
        "stock_id": db_stock.id,
        "symbol": db_stock.symbol,
        "market": db_stock.market,
        "investment_profile": profile,
        "portfolio_role_label": portfolio_role_label(profile.get("portfolio_role", "watch")),
    }


@router.put("/{stock_id}/investment-profile", response_model=StockResponse)
def update_investment_profile(
    stock_id: int,
    body: InvestmentProfileUpdate,
    db: Session = Depends(get_db),
):
    """更新股票长线投资计划。"""
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    current = normalize_investment_profile(db_stock.investment_profile)
    patch = body.model_dump(exclude_unset=True)
    current.update(patch)
    db_stock.investment_profile = normalize_investment_profile(current)
    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock)


@router.get("/{stock_id}/investment-profile/evaluate")
def evaluate_investment_profile(
    stock_id: int,
    price: float | None = Query(None, gt=0, description="评估用现价，缺省则用持仓成本"),
    db: Session = Depends(get_db),
):
    """评估当前是否触发计划内加仓。"""
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    snap = _portfolio_snapshot_for_stock(db, db_stock)
    current_price = price or snap.get("avg_cost")
    result = evaluate_add_plan(
        db_stock.investment_profile,
        current_price=current_price,
        avg_cost=snap.get("avg_cost"),
        position_value=float(snap.get("position_value") or 0),
        total_assets=float(snap.get("total_assets") or 0),
        available_cash=float(snap.get("available_cash") or 0),
        has_buy_today=bool(snap.get("has_buy_today")),
        market=db_stock.market or "CN",
    )
    return {
        "stock_id": db_stock.id,
        "symbol": db_stock.symbol,
        "market": db_stock.market,
        "current_price": current_price,
        "total_assets": snap.get("total_assets"),
        "total_market_value": snap.get("total_market_value"),
        "available_cash": snap.get("available_cash"),
        "position_value": snap.get("position_value"),
        **result,
    }


@router.put("/{stock_id}", response_model=StockResponse)
def update_stock(stock_id: int, stock: StockUpdate, db: Session = Depends(get_db)):
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    for key, value in stock.model_dump(exclude_unset=True).items():
        setattr(db_stock, key, value)

    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock)


@router.delete("/{stock_id}")
def delete_stock(stock_id: int, db: Session = Depends(get_db)):
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    # 删除股票前，要求先清理持仓，避免误删资产数据。
    has_position = db.query(Position.id).filter(Position.stock_id == stock_id).first()
    if has_position:
        raise HTTPException(400, "该股票存在持仓，请先删除持仓后再删除股票")

    # SQLite 默认可能不启用 FK 级联，手动清理提醒数据避免孤儿记录。
    rule_ids = [
        row[0]
        for row in db.query(PriceAlertRule.id).filter(
            PriceAlertRule.stock_id == stock_id
        ).all()
    ]
    if rule_ids:
        db.query(PriceAlertHit).filter(PriceAlertHit.rule_id.in_(rule_ids)).delete(
            synchronize_session=False
        )
    db.query(PriceAlertHit).filter(PriceAlertHit.stock_id == stock_id).delete(
        synchronize_session=False
    )
    db.query(PriceAlertRule).filter(PriceAlertRule.stock_id == stock_id).delete(
        synchronize_session=False
    )
    db.query(StockAgent).filter(StockAgent.stock_id == stock_id).delete(
        synchronize_session=False
    )

    db.delete(db_stock)
    db.commit()
    return {"ok": True}


@router.put("/{stock_id}/agents", response_model=StockResponse)
def update_stock_agents(stock_id: int, body: StockAgentUpdate, db: Session = Depends(get_db)):
    """更新股票关联的 Agent 列表（含调度配置和 AI/通知覆盖）"""
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    for item in body.agents:
        agent = db.query(AgentConfig).filter(AgentConfig.name == item.agent_name).first()
        if not agent:
            raise HTTPException(400, f"Agent {item.agent_name} 不存在")
        agent_kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
        if agent_kind != AGENT_KIND_WORKFLOW:
            raise HTTPException(400, f"Agent {item.agent_name} 为内部能力，不支持绑定到股票")

    # 清除旧关联，重建
    db.query(StockAgent).filter(StockAgent.stock_id == stock_id).delete()
    for item in body.agents:
        db.add(StockAgent(
            stock_id=stock_id,
            agent_name=item.agent_name,
            schedule=item.schedule,
            ai_model_id=item.ai_model_id,
            notify_channel_ids=item.notify_channel_ids,
        ))

    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock)


class StockAgentTriggerBody(BaseModel):
    """手动触发 Agent 的可选请求体。"""
    analyst_types: list[str] | None = None


@router.post("/{stock_id}/agents/{agent_name}/trigger")
async def trigger_stock_agent(
    stock_id: int,
    agent_name: str,
    body: StockAgentTriggerBody | None = None,
    bypass_throttle: bool = False,
    bypass_market_hours: bool = False,
    allow_unbound: bool = False,
    wait: bool = False,
    force_refresh: bool = False,
    symbol: str = Query(""),
    market: str = Query("CN"),
    name: str = Query(""),
    db: Session = Depends(get_db),
):
    """手动触发单只股票 Agent。

    - 正常模式：传有效 stock_id
    - 无绑定模式：stock_id<=0 且传 symbol/market（需 allow_unbound=true）
    - 无绑定模式默认禁用通知（仅生成建议）
    - 默认异步执行（立即返回），传 wait=true 可同步等待结果
    """
    sa = None
    trigger_stock = None
    suppress_notify = stock_id <= 0

    if stock_id > 0:
        db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not db_stock:
            raise HTTPException(404, "股票不存在")

        sa = db.query(StockAgent).filter(
            StockAgent.stock_id == stock_id, StockAgent.agent_name == agent_name
        ).first()
        if not sa and not allow_unbound:
            raise HTTPException(400, f"股票未关联 Agent {agent_name}")
        if not sa and allow_unbound:
            # 允许无绑定触发时，至少确保 Agent 存在。
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if not agent:
                raise HTTPException(400, f"Agent {agent_name} 不存在")
        trigger_stock = db_stock
    else:
        symbol = (symbol or "").strip()
        if not symbol:
            raise HTTPException(400, "当 stock_id<=0 时，symbol 不能为空")
        if not allow_unbound:
            raise HTTPException(400, "当 stock_id<=0 时，需设置 allow_unbound=true")

        market = (market or "CN").strip().upper() or "CN"
        name = (name or "").strip() or symbol
        db_stock = db.query(Stock).filter(
            Stock.symbol == symbol, Stock.market == market
        ).first()
        if db_stock:
            sa = db.query(StockAgent).filter(
                StockAgent.stock_id == db_stock.id, StockAgent.agent_name == agent_name
            ).first()
            trigger_stock = db_stock
        else:
            # 不落库：用于详情弹窗未持仓且未关注股票的一次性分析。
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if not agent:
                raise HTTPException(400, f"Agent {agent_name} 不存在")
            trigger_stock = SimpleNamespace(
                id=0,
                symbol=symbol,
                name=name,
                market=market,
                security_type=_infer_security_type_for_symbol(symbol, market),
            )

    logger.info(
        f"手动触发 Agent {agent_name} - {trigger_stock.name}({trigger_stock.symbol})"
    )

    from server import trigger_agent_for_stock
    import time as _time

    analyst_types_override: list[str] | None = None
    if agent_name == "tradingagents" and body and body.analyst_types:
        from src.agents.tradingagents.llm_adapter import VALID_ANALYSTS

        requested = [str(x).strip() for x in body.analyst_types if str(x).strip()]
        invalid = [a for a in requested if a not in VALID_ANALYSTS]
        if invalid:
            raise HTTPException(
                400,
                f"非法 analyst_types: {invalid}; 合法值: {sorted(VALID_ANALYSTS)}",
            )
        analyst_types_override = requested

    # 幂等性兜底:TradingAgents 单次 3-5 分钟,前端误操作/双击可能并发触发同一标的。
    # 后端先查"该 symbol 是否有真正在跑的 TA 任务",有则返回现有 trace_id(不启新任务)。
    # force_refresh=true 时跳过去重,允许用户主动强制重跑(老任务自然终止,新 trace_id)。
    if agent_name == "tradingagents" and not force_refresh:
        from src.web.api.agents import find_active_tradingagents_trace
        existing_trace = find_active_tradingagents_trace(db, trigger_stock.symbol)
        if existing_trace:
            logger.info(
                f"[trigger 幂等] {trigger_stock.symbol} 已有在跑任务 trace={existing_trace},"
                f"复用而非启新任务"
            )
            return {
                "queued": False,
                "trace_id": existing_trace,
                "message": "已有正在执行的深度分析,返回现有任务进度",
                "deduplicated": True,
            }

    # 预生成 trace_id,返回给前端用于轮询进度
    trace_id = f"man-{agent_name}-{trigger_stock.symbol}-{int(_time.time() * 1000)}"

    # 立刻写一条"任务已触发"进度日志,保证前端 polling 第一拍就能看到 running。
    # 否则 trigger_agent_for_stock 内部要先 await agent.collect()(美股拉 yfinance 数据
    # 可能 30s+),期间没有任何 ta_progress 日志 → 前端 progress 接口返回 not_found
    # → 60s grace 过后前端 reset 到 idle,看起来像"进度卡死自动退回"。
    if agent_name == "tradingagents":
        try:
            from src.core.log_context import log_context
            with log_context(
                trace_id=trace_id,
                agent_name="tradingagents",
                event="ta_progress",
                tags={"stage": "task_triggered", "action": "triggered"},
            ):
                logger.info(
                    f"[TA] 任务已触发 - {trigger_stock.symbol} (trace={trace_id})"
                )
        except Exception as e:
            logger.warning(f"[TA] 写触发日志失败,不影响主流程: {e}")

    if not wait:
        # 异步模式：后台执行，立即返回
        sa_id = sa.id if sa else None

        def _runner():
            try:
                asyncio.run(trigger_agent_for_stock(
                    agent_name,
                    trigger_stock,
                    stock_agent_id=sa_id,
                    bypass_throttle=bypass_throttle,
                    bypass_market_hours=bypass_market_hours,
                    suppress_notify=suppress_notify,
                    trace_id=trace_id,
                    force_refresh=force_refresh,
                    analyst_types_override=analyst_types_override,
                ))
                logger.info(f"Agent {agent_name} 后台执行完成 - {trigger_stock.symbol}")
            except Exception:
                logger.exception(f"Agent {agent_name} 后台执行失败 - {trigger_stock.symbol}")

        t = threading.Thread(
            target=_runner,
            name=f"stock-trigger-{agent_name}-{trigger_stock.symbol}",
            daemon=True,
        )
        t.start()
        return {"queued": True, "trace_id": trace_id, "message": "已提交后台执行"}

    # 同步模式：等待结果返回
    try:
        result = await trigger_agent_for_stock(
            agent_name,
            trigger_stock,
            stock_agent_id=sa.id if sa else None,
            bypass_throttle=bypass_throttle,
            bypass_market_hours=bypass_market_hours,
            suppress_notify=suppress_notify,
            trace_id=trace_id,
            force_refresh=force_refresh,
            analyst_types_override=analyst_types_override,
        )
        logger.info(f"Agent {agent_name} 执行完成 - {trigger_stock.symbol}")
        return {
            "result": result,
            "trace_id": trace_id,
            "code": int(result.get("code", 0)),
            "success": bool(result.get("success", True)),
            "message": result.get("message", "ok"),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Agent {agent_name} 执行失败 - {trigger_stock.symbol}: {e}")
        raise HTTPException(500, f"Agent 执行失败: {e}")
