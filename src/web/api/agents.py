import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import AgentConfig, AgentRun
from src.core.schedule_parser import preview_schedule
from src.core.schedule_parser import count_runs_within
from src.config import Settings

logger = logging.getLogger(__name__)


def _format_datetime(dt) -> str:
    """格式化时间为带时区的 ISO 格式"""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


router = APIRouter()


@router.get("/health")
def agents_health(db: Session = Depends(get_db)):
    """调度健康概览（用于排查调度/时区/触发问题）"""
    tz = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz)
    except Exception:
        tzinfo = timezone.utc

    now = datetime.now(tzinfo)
    horizon = now + timedelta(hours=24)

    agents = db.query(AgentConfig).order_by(AgentConfig.name.asc()).all()
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

        out.append(
            {
                "name": a.name,
                "display_name": a.display_name,
                "enabled": a.enabled,
                "schedule": a.schedule or "",
                "execution_mode": a.execution_mode or "batch",
                "next_runs": next_runs,
                "last_run": last_run,
            }
        )

    return {
        "timezone": tz,
        "summary": {
            "next_24h_count": next_24h_count,
            "recent_failed_count": recent_failed_count,
        },
        "agents": out,
    }


class AgentConfigUpdate(BaseModel):
    enabled: bool | None = None
    schedule: str | None = None
    ai_model_id: int | None = None
    notify_channel_ids: list[int] | None = None
    config: dict | None = None


class AgentConfigResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: str
    enabled: bool
    schedule: str
    execution_mode: str  # batch / single
    ai_model_id: int | None
    notify_channel_ids: list[int]
    config: dict

    class Config:
        from_attributes = True


class AgentRunResponse(BaseModel):
    id: int
    agent_name: str
    status: str
    result: str
    error: str
    duration_ms: int
    created_at: str

    class Config:
        from_attributes = True


@router.get("", response_model=list[AgentConfigResponse])
def list_agents(db: Session = Depends(get_db)):
    agents = db.query(AgentConfig).all()
    return [_agent_to_response(a) for a in agents]


def _agent_to_response(agent: AgentConfig) -> dict:
    return {
        "id": agent.id,
        "name": agent.name,
        "display_name": agent.display_name,
        "description": agent.description,
        "enabled": agent.enabled,
        "schedule": agent.schedule or "",
        "execution_mode": agent.execution_mode or "batch",
        "ai_model_id": agent.ai_model_id,
        "notify_channel_ids": agent.notify_channel_ids or [],
        "config": agent.config or {},
    }


@router.put("/{agent_name}", response_model=AgentConfigResponse)
def update_agent(
    agent_name: str, update: AgentConfigUpdate, db: Session = Depends(get_db)
):
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise HTTPException(404, f"Agent {agent_name} 不存在")

    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(agent, key, value)

    db.commit()
    db.refresh(agent)
    return _agent_to_response(agent)


@router.get("/schedule/preview")
def preview_schedule_expr(schedule: str, count: int = 5):
    """预览某个 schedule 表达式接下来几次触发时间（按调度时区）"""
    tz = Settings().app_timezone or "UTC"
    if not schedule:
        return {"schedule": "", "timezone": tz, "next_runs": []}

    try:
        runs = preview_schedule(schedule, count=count, timezone=tz)
    except Exception as e:
        raise HTTPException(400, f"schedule 无法解析: {e}")

    return {
        "schedule": schedule,
        "timezone": tz,
        "next_runs": [r.isoformat() for r in runs],
    }


@router.get("/{agent_name}/schedule/preview")
def preview_agent_schedule(
    agent_name: str, count: int = 5, db: Session = Depends(get_db)
):
    """预览某个 Agent 接下来几次的触发时间（按调度时区）"""
    tz = Settings().app_timezone or "UTC"
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise HTTPException(404, f"Agent {agent_name} 不存在")
    if not agent.schedule:
        return {"schedule": "", "timezone": tz, "next_runs": []}

    try:
        runs = preview_schedule(agent.schedule, count=count, timezone=tz)
    except Exception as e:
        raise HTTPException(400, f"schedule 无法解析: {e}")

    return {
        "schedule": agent.schedule,
        "timezone": tz,
        "next_runs": [r.isoformat() for r in runs],
    }


@router.delete("/{agent_name}")
def delete_agent(agent_name: str, db: Session = Depends(get_db)):
    """删除 Agent 配置"""
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise HTTPException(404, f"Agent {agent_name} 不存在")

    # 删除关联的 stock_agents 记录
    from src.web.models import StockAgent

    db.query(StockAgent).filter(StockAgent.agent_name == agent_name).delete()

    db.delete(agent)
    db.commit()
    return {"ok": True, "message": f"Agent {agent_name} 已删除"}


@router.post("/{agent_name}/trigger")
async def trigger_agent_endpoint(agent_name: str, db: Session = Depends(get_db)):
    """手动触发 Agent 执行"""
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise HTTPException(404, f"Agent {agent_name} 不存在")
    if not agent.enabled:
        raise HTTPException(400, f"Agent {agent_name} 未启用")

    from server import trigger_agent

    try:
        result = await trigger_agent(agent_name)
        return {"ok": True, "message": result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Agent 执行失败: {e}")


@router.get("/{agent_name}/history", response_model=list[AgentRunResponse])
def get_agent_history(agent_name: str, limit: int = 20, db: Session = Depends(get_db)):
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.agent_name == agent_name)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        AgentRunResponse(
            id=run.id,
            agent_name=run.agent_name,
            status=run.status or "",
            result=run.result or "",
            error=run.error or "",
            duration_ms=run.duration_ms or 0,
            created_at=_format_datetime(run.created_at),
        )
        for run in runs
    ]


@router.post("/intraday/scan")
async def scan_intraday(analyze: bool = False, db: Session = Depends(get_db)):
    """
    实时扫描盘中监测 Agent 关联的股票

    设计说明：
    - 只扫描启用了「盘中监测」Agent 的股票
    - 返回所有股票的实时行情和技术分析
    - analyze=True 时调用 AI 分析，返回结构化建议

    Args:
        analyze: 是否调用 AI 分析生成操作建议（默认 False）
    """
    from server import (
        load_watchlist_for_agent,
        load_portfolio_for_agent,
        build_context,
    )
    from src.collectors.akshare_collector import AkshareCollector
    from src.collectors.kline_collector import KlineCollector
    from src.models.market import MarketCode, MARKETS
    from src.agents.intraday_monitor import IntradayMonitorAgent
    from src.core.analysis_history import get_latest_analysis, get_analysis
    from src.core.suggestion_pool import save_suggestion

    agent_name = "intraday_monitor"
    agent_cfg = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    agent_kwargs = agent_cfg.config if agent_cfg and agent_cfg.config else {}

    # 只获取关联了盘中监测 Agent 的股票
    watchlist = load_watchlist_for_agent(agent_name)

    if not watchlist:
        return {
            "stocks": [],
            "message": "请先为股票启用「盘中监测」Agent",
            "scanned_count": 0,
            "has_watchlist": False,
        }

    # 检查是否有市场在交易
    any_trading = any(m.is_trading_time() for m in MARKETS.values())
    if not any_trading:
        return {
            "stocks": [],
            "message": "当前非交易时段",
            "scanned_count": len(watchlist),
            "is_trading": False,
            "has_watchlist": True,
        }

    # 获取持仓信息
    portfolio = load_portfolio_for_agent(agent_name)

    # 按市场分组采集行情
    market_symbols: dict[MarketCode, list] = {}
    stock_market_map: dict[str, MarketCode] = {}
    for stock in watchlist:
        market_symbols.setdefault(stock.market, []).append(stock.symbol)
        stock_market_map[stock.symbol] = stock.market

    all_quotes = []
    for market_code, symbols in market_symbols.items():
        try:
            collector = AkshareCollector(market_code)
            stocks = await collector.get_stock_data(symbols)
            all_quotes.extend(stocks)
        except Exception as e:
            logger.error(f"采集 {market_code.value} 行情失败: {e}")

    quote_by_symbol = {q.symbol: q for q in all_quotes}

    # 解析 Agent 阈值配置（用于异动标记与提示 AI）
    try:
        monitor_agent = IntradayMonitorAgent(bypass_throttle=True, **agent_kwargs)
    except TypeError:
        # 兼容旧配置（字段不匹配时回退）
        monitor_agent = IntradayMonitorAgent(bypass_throttle=True)

    # 获取历史分析（给 AI 作为上下文）
    try:
        daily_analysis = get_latest_analysis(
            agent_name="daily_report",
            stock_symbol="*",
        )
        premarket_analysis = get_analysis(
            agent_name="premarket_outlook",
            stock_symbol="*",
        )
    except Exception:
        daily_analysis = None
        premarket_analysis = None

    # 构建返回数据
    results = []
    for quote in all_quotes:
        change_pct = quote.change_pct or 0
        market = stock_market_map.get(quote.symbol, MarketCode.CN)

        # 获取持仓信息
        positions = portfolio.get_positions_for_stock(quote.symbol)
        has_position = len(positions) > 0
        cost_price = positions[0].cost_price if positions else None
        trading_style = positions[0].trading_style if positions else None
        pnl_pct = None
        if cost_price and quote.current_price:
            pnl_pct = (quote.current_price - cost_price) / cost_price * 100

        # 获取技术分析
        kline_summary = None
        try:
            kline_collector = KlineCollector(market)
            kline_summary = kline_collector.get_kline_summary(quote.symbol)
        except Exception as e:
            logger.warning(f"获取 {quote.symbol} K线失败: {e}")

        # 判断异动类型
        alert_type = None
        if abs(change_pct) >= getattr(monitor_agent, "price_alert_threshold", 3.0):
            alert_type = "急涨" if change_pct > 0 else "急跌"

        results.append(
            {
                "symbol": quote.symbol,
                "name": quote.name,
                "market": market.value,
                "current_price": quote.current_price,
                "change_pct": change_pct,
                "change_amount": quote.change_amount,
                "open_price": quote.open_price,
                "high_price": quote.high_price,
                "low_price": quote.low_price,
                "prev_close": quote.prev_close,
                "volume": quote.volume,
                "turnover": quote.turnover,
                "alert_type": alert_type,
                "has_position": has_position,
                "cost_price": cost_price,
                "pnl_pct": pnl_pct,
                "trading_style": trading_style,
                "kline": kline_summary,
                "suggestion": None,  # AI 建议
            }
        )

    # AI 分析
    if analyze and results:
        try:
            context = build_context(agent_name)
            agent = monitor_agent

            for item in results:
                try:
                    stock_data = quote_by_symbol.get(item["symbol"])
                    if not stock_data:
                        continue

                    data = {
                        "stock_data": stock_data,
                        "stocks": [stock_data],
                        "kline_summary": item["kline"],
                        "daily_analysis": daily_analysis.content
                        if daily_analysis
                        else None,
                        "premarket_analysis": premarket_analysis.content
                        if premarket_analysis
                        else None,
                    }

                    # P1: event-driven gate (reduce AI calls)
                    try:
                        if getattr(agent, "event_only", False):
                            from src.core.intraday_event_gate import check_and_update

                            decision = check_and_update(
                                symbol=item["symbol"],
                                change_pct=item.get("change_pct"),
                                volume_ratio=(item.get("kline") or {}).get(
                                    "volume_ratio"
                                ),
                                kline_summary=item.get("kline"),
                                price_threshold=getattr(
                                    agent, "price_alert_threshold", 3.0
                                ),
                                volume_threshold=getattr(
                                    agent, "volume_alert_ratio", 2.0
                                ),
                            )
                            if not decision.should_analyze:
                                item["suggestion"] = {
                                    "action": "watch",
                                    "action_label": "观望",
                                    "signal": "",
                                    "reason": "",
                                    "should_alert": False,
                                    "skipped": True,
                                    "skip_reason": "no_event",
                                }
                                continue
                            data["event_gate"] = {"reasons": decision.reasons}
                    except Exception:
                        pass

                    system_prompt, user_content = agent.build_prompt(data, context)
                    response = await context.ai_client.chat(system_prompt, user_content)

                    # 解析结构化建议
                    suggestion = agent._parse_suggestion(response)
                    suggestion["raw"] = response.strip()[:200]

                    item["suggestion"] = suggestion
                    # 写入建议池（用于持仓页展示），避免频繁扫描导致“持有/观望”覆盖太久
                    expires_hours = 4 if suggestion.get("should_alert", True) else 1
                    save_suggestion(
                        stock_symbol=item["symbol"],
                        stock_name=item["name"] or "",
                        action=suggestion.get("action", "watch"),
                        action_label=suggestion.get("action_label", "观望"),
                        signal=suggestion.get("signal", ""),
                        reason=suggestion.get("reason", ""),
                        agent_name=agent_name,
                        agent_label=agent.display_name,
                        expires_hours=expires_hours,
                        prompt_context=user_content,
                        ai_response=response,
                        meta={
                            "quote": {
                                "current_price": item.get("current_price"),
                                "change_pct": item.get("change_pct"),
                            },
                            "kline_meta": {
                                "computed_at": (item.get("kline") or {}).get(
                                    "computed_at"
                                ),
                                "asof": (item.get("kline") or {}).get("asof"),
                            },
                            "event_gate": data.get("event_gate"),
                        },
                    )

                except Exception as e:
                    item["suggestion"] = {
                        "action": "watch",
                        "action_label": "观望",
                        "signal": "",
                        "reason": f"分析失败: {e}",
                        "should_alert": False,
                    }
                    logger.error(f"AI 分析失败 {item['symbol']}: {e}")

        except Exception as e:
            logger.error(f"构建 Agent 上下文失败: {e}")

    return {
        "stocks": results,
        "scanned_count": len(watchlist),
        "is_trading": True,
        "has_watchlist": True,
        "available_funds": portfolio.total_available_funds,
    }
