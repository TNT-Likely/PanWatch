import asyncio
from copy import deepcopy
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.platform.persistence.database import get_db
from src.platform.persistence.models import AgentConfig, AgentRun, LogEntry
from src.modules.automation.scheduling_policy import schedule_plans, request_scheduler_reload
from src.platform.scheduling.schedule_parser import parse_schedule, preview_schedule
from src.platform.scheduling.schedule_parser import count_runs_within
from src.platform.runtime.config import Settings
from src.web.errors import ai_api_error, api_error
from src.modules.automation.agent_catalog import (
    AGENT_KIND_CAPABILITY,
    AGENT_KIND_WORKFLOW,
    infer_agent_kind,
)
from src.modules.automation.agent_runs import ACTIVE_RUN_TTL_SEC, _as_utc

logger = logging.getLogger(__name__)

_SNAPSHOT_CACHE_LOCK = threading.Lock()
_SNAPSHOT_CACHE: dict[str, tuple[float, dict]] = {}
_SNAPSHOT_TTL_SECONDS = 12.0


def _build_snapshot_cache_key(watchlist) -> str:
    return "|".join(sorted(f"{stock.market.value}:{stock.symbol}" for stock in watchlist))


def _get_snapshot_cache(key: str) -> dict | None:
    with _SNAPSHOT_CACHE_LOCK:
        hit = _SNAPSHOT_CACHE.get(key)
        if not hit:
            return None
        if time.monotonic() - hit[0] > _SNAPSHOT_TTL_SECONDS:
            del _SNAPSHOT_CACHE[key]
            return None
        return deepcopy(hit[1])


def _set_snapshot_cache(key: str, payload: dict) -> None:
    with _SNAPSHOT_CACHE_LOCK:
        _SNAPSHOT_CACHE[key] = (time.monotonic(), deepcopy(payload))


def _format_datetime(dt, tz: str | None = None) -> str:
    """格式化时间为当前时区的 ISO 格式。

    说明：SQLite 存储的时间通常没有 tzinfo，按 UTC 解释后再转换到 app_timezone。
    """

    if not dt:
        return ""

    tz_name = tz or Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(tzinfo).isoformat()


def _spawn_async_run(fn, *args, name: str) -> None:
    """Run an async function in a dedicated thread."""

    def _runner():
        try:
            asyncio.run(fn(*args))
        except Exception:
            logger.exception(f"后台任务失败: {name}")

    t = threading.Thread(target=_runner, name=name, daemon=True)
    t.start()


router = APIRouter()


def _public_agent_config(config: dict | None) -> dict:
    value = dict(config or {})
    value.pop("output_language", None)
    return value


@router.get("/health")
def agents_health(
    include_internal: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """调度健康概览（用于排查调度/时区/触发问题）"""
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
    agents = query.order_by(AgentConfig.display_order.asc(), AgentConfig.name.asc()).all()
    out = []
    next_24h_count = 0
    recent_failed_count = 0

    for a in agents:
        next_runs: list[str] = []
        if a.enabled:
            try:
                runs = []
                for plan in schedule_plans(db, a):
                    runs.extend(preview_schedule(plan.schedule, count=3, timezone=tz, start=now,
                        markets=plan.markets, trading_hours_only=a.name == "intraday_monitor"))
                    next_24h_count += count_runs_within(plan.schedule, start=now, end=horizon,
                        timezone=tz, markets=plan.markets, trading_hours_only=a.name == "intraday_monitor")
                next_runs = [r.isoformat() for r in sorted(set(runs))[:3]]
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
                "created_at": _format_datetime(last.created_at, tz=tz),
                "duration_ms": last.duration_ms or 0,
                "error": last.error or "",
            }
            if a.enabled and (last.status or "") == "failed":
                recent_failed_count += 1

        out.append(
            {
                "name": a.name,
                "display_name": a.display_name,
                "kind": a.kind or infer_agent_kind(a.name),
                "visible": bool(a.visible),
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
    visible: bool | None = None


class AgentConfigResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: str
    kind: str
    visible: bool
    lifecycle_status: str
    replaced_by: str
    display_order: int
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
    trace_id: str = ""
    trigger_source: str = ""
    notify_attempted: bool = False
    notify_sent: bool = False
    context_chars: int = 0
    model_label: str = ""
    status: str
    result: str
    error: str
    duration_ms: int
    created_at: str

    class Config:
        from_attributes = True


@router.get("", response_model=list[AgentConfigResponse])
def list_agents(
    include_internal: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    query = db.query(AgentConfig)
    if not include_internal:
        query = query.filter(
            AgentConfig.kind == AGENT_KIND_WORKFLOW,
            AgentConfig.visible == True,
        )
    agents = query.order_by(AgentConfig.display_order.asc(), AgentConfig.name.asc()).all()
    return [_agent_to_response(a) for a in agents]


def _agent_to_response(agent: AgentConfig) -> dict:
    kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
    return {
        "id": agent.id,
        "name": agent.name,
        "display_name": agent.display_name,
        "description": agent.description,
        "kind": kind,
        "visible": bool(agent.visible),
        "lifecycle_status": agent.lifecycle_status or "active",
        "replaced_by": agent.replaced_by or "",
        "display_order": int(agent.display_order or 0),
        "enabled": agent.enabled,
        "schedule": agent.schedule or "",
        "execution_mode": agent.execution_mode or "batch",
        "ai_model_id": agent.ai_model_id,
        "notify_channel_ids": agent.notify_channel_ids or [],
        "config": _public_agent_config(agent.config),
    }


@router.get("/capabilities", response_model=list[AgentConfigResponse])
def list_capabilities(db: Session = Depends(get_db)):
    rows = (
        db.query(AgentConfig)
        .filter(AgentConfig.kind == AGENT_KIND_CAPABILITY)
        .order_by(AgentConfig.display_order.asc(), AgentConfig.name.asc())
        .all()
    )
    return [_agent_to_response(a) for a in rows]


@router.put("/{agent_name}", response_model=AgentConfigResponse)
def update_agent(
    agent_name: str, update: AgentConfigUpdate, db: Session = Depends(get_db)
):
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise api_error(404, "agent_not_found", f"Agent {agent_name} 不存在")

    if update.schedule:
        try:
            parse_schedule(update.schedule)
        except ValueError as exc:
            raise api_error(400, "agent_schedule_invalid", "调度表达式无法解析") from exc
    for key, value in update.model_dump(exclude_unset=True).items():
        if key == "config":
            value = _public_agent_config(value)
        setattr(agent, key, value)

    # capability 仅支持手动调用，不参与调度。
    kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
    if kind == AGENT_KIND_CAPABILITY:
        agent.enabled = False
        agent.schedule = ""

    db.commit()
    db.refresh(agent)
    request_scheduler_reload()
    return _agent_to_response(agent)


@router.get("/schedule/preview")
def preview_schedule_expr(schedule: str, count: int = Query(default=5, ge=1, le=50),
                          agent_name: str = "", market: str = "", db: Session = Depends(get_db)):
    """Preview actual eligible runs for an agent/market; keep raw Cron validation available."""
    tz = Settings().app_timezone or "UTC"
    markets = None
    if market:
        from src.platform.scheduling.trading_calendar import _to_market_code
        code = _to_market_code(market)
        if code is None:
            raise api_error(400, "market_invalid", "市场代码无效")
        markets = [code]
    elif agent_name:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        if agent is None:
            raise api_error(404, "agent_not_found", "Agent 不存在")
        plans = schedule_plans(db, agent, global_schedule=schedule)
        markets = sorted({market for plan in plans if plan.stock_agent_id is None for market in plan.markets})
    try:
        runs = preview_schedule(schedule, count=count, timezone=tz, markets=markets,
                                trading_hours_only=agent_name == "intraday_monitor") if schedule else []
    except ValueError as exc:
        raise api_error(400, "agent_schedule_invalid", "调度表达式无法解析") from exc
    return {"schedule": schedule, "timezone": tz, "next_runs": [r.isoformat() for r in runs],
            "calendar_filtered": markets is not None}


@router.get("/{agent_name}/schedule/preview")
def preview_agent_schedule(agent_name: str, count: int = Query(default=5, ge=1, le=50),
                           db: Session = Depends(get_db)):
    tz = Settings().app_timezone or "UTC"
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise api_error(404, "agent_not_found", f"Agent {agent_name} 不存在")
    try:
        runs = [run for plan in schedule_plans(db, agent)
                for run in preview_schedule(plan.schedule, count=count, timezone=tz,
                    markets=plan.markets, trading_hours_only=agent_name == "intraday_monitor")]
    except ValueError as exc:
        raise api_error(400, "agent_schedule_invalid", "调度表达式无法解析") from exc
    return {"schedule": agent.schedule, "timezone": tz,
            "next_runs": [r.isoformat() for r in sorted(set(runs))[:count]], "calendar_filtered": True}


@router.delete("/{agent_name}")
def delete_agent(agent_name: str, db: Session = Depends(get_db)):
    """删除 Agent 配置"""
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise api_error(404, "agent_not_found", f"Agent {agent_name} 不存在")

    # 删除关联的 stock_agents 记录
    from src.platform.persistence.models import StockAgent

    db.query(StockAgent).filter(StockAgent.agent_name == agent_name).delete()

    db.delete(agent)
    db.commit()
    request_scheduler_reload()
    return {"ok": True, "message": f"Agent {agent_name} 已删除"}


@router.post("/{agent_name}/trigger")
async def trigger_agent_endpoint(
    agent_name: str,
    wait: bool = Query(
        default=False,
        description="是否同步等待执行完成；batch agent 默认异步排队",
    ),
    db: Session = Depends(get_db),
):
    """手动触发 Agent 执行"""
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise api_error(404, "agent_not_found", f"Agent {agent_name} 不存在")
    agent_kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
    if agent_kind == AGENT_KIND_WORKFLOW and not agent.enabled:
        raise api_error(400, "agent_not_enabled", f"Agent {agent_name} 未启用")

    from server import trigger_agent

    try:
        # Batch agents can take long; allow caller to choose wait mode.
        if (
            agent_kind == AGENT_KIND_WORKFLOW
            and agent_name in {"daily_report", "premarket_outlook"}
            and not wait
        ):
            _spawn_async_run(
                trigger_agent, agent_name, name=f"trigger_agent:{agent_name}"
            )
            return {"ok": True, "queued": True, "message": "已提交后台执行"}

        result = await trigger_agent(agent_name)
        return {"ok": True, "queued": False, "message": result}
    except ValueError as e:
        logger.warning("Agent %s 执行参数无效: %s", agent_name, e)
        raise api_error(400, "agent_trigger_invalid", "Agent 执行参数无效") from e
    except Exception as e:
        logger.exception("Agent %s 执行失败", agent_name)
        raise ai_api_error(e) from e


@router.get("/tradingagents/running")
def find_running_for_stock(
    stock_symbol: str = Query(..., description="股票代码"),
    lookback_minutes: int = Query(default=30, ge=1, le=120),
    db: Session = Depends(get_db),
):
    """查找某只股票最近 N 分钟内是否有 TradingAgents 运行任务。

    用于 DeepAnalysisModal 重新打开时,**后端权威源**判断是否有正在跑或刚完成的任务,
    比 localStorage 更可靠(跨浏览器/无痕/换设备都能查到)。

    判断逻辑:
    1. 优先查 agent_runs 中未过期的 running 记录（覆盖数据采集阶段）
    2. 再查 log_entries 中 event=ta_progress + trace_id 含 -{symbol}- 的最新一条
    3. 看对应 trace_id 在 agent_runs 表是否有完成记录
       - 有完成记录 + status=success → 已完成 (前端可拉 latest 结果显示)
       - 有完成记录 + status=failed → 已失败
       - 无完成记录 + 日志在 30 分钟内 → running
        - 无任何生命周期记录或日志 → none

    Returns:
        {"trace_id": str|None, "status": "running"|"success"|"failed"|"none"}
    """
    # 先读持久化生命周期记录：采集阶段没有 ta_progress 时也能恢复。
    from src.modules.automation.agent_runs import find_active_tradingagents_trace

    active_trace = find_active_tradingagents_trace(db, stock_symbol)
    if active_trace:
        return {
            "trace_id": active_trace,
            "status": "running",
            "last_activity_at": None,
        }

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)

    latest_log = (
        db.query(LogEntry)
        .filter(
            LogEntry.event == "ta_progress",
            LogEntry.agent_name == "tradingagents",
            LogEntry.timestamp >= cutoff,
            LogEntry.trace_id.like(f"%-{stock_symbol}-%"),
        )
        .order_by(LogEntry.timestamp.desc())
        .first()
    )

    if not latest_log or not latest_log.trace_id:
        return {"trace_id": None, "status": "none"}

    trace_id = latest_log.trace_id

    # 检查该 trace 是否已有完成记录
    run = (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == trace_id)
        .order_by(AgentRun.id.desc())
        .first()
    )

    # 没有 run 记录时,看最后日志距今 — 超过 STALE_THRESHOLD 视为僵尸 running
    # (server 重启 / 工作线程死掉),前端可据此 reset 到 idle 允许重新分析
    status = run.status if run else "running"
    if status == "running":
        created_at = _as_utc(run.created_at) if run else None
        if created_at and (datetime.now(timezone.utc) - created_at).total_seconds() > ACTIVE_RUN_TTL_SEC:
            status = "stale"
        last_ts = latest_log.timestamp
        if status == "running" and last_ts and last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        if status == "running" and last_ts:
            idle_sec = (datetime.now(timezone.utc) - last_ts).total_seconds()
            if idle_sec > 300:  # 5 分钟无新进度 → stale
                status = "stale"

    return {
        "trace_id": trace_id,
        "status": status,
        "last_activity_at": _format_datetime(latest_log.timestamp),
    }


@router.get("/tradingagents/latest")
def get_tradingagents_latest(
    stock_symbol: str = Query(..., description="股票代码,如 300418"),
    db: Session = Depends(get_db),
):
    """获取某只股票最近一次 TradingAgents 深度分析的完整结果(含 raw_data)。

    /history 端点 cherry-pick 字段不含 raw_data,这里专门为深度分析弹窗
    暴露完整字段(suggestion / debate_history / analyst_reports / cost_usd 等)。
    """
    from src.platform.persistence.models import AnalysisHistory

    record = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == "tradingagents",
            AnalysisHistory.stock_symbol == stock_symbol,
        )
        .order_by(
            AnalysisHistory.analysis_date.desc(),
            AnalysisHistory.updated_at.desc(),
            AnalysisHistory.id.desc(),
        )
        .first()
    )
    if not record:
        return None

    return {
        "id": record.id,
        "agent_name": record.agent_name,
        "stock_symbol": record.stock_symbol,
        "analysis_date": record.analysis_date,
        "title": record.title or "",
        "content": record.content,
        "raw_data": record.raw_data or {},
        "created_at": _format_datetime(record.created_at),
        "updated_at": _format_datetime(record.updated_at),
    }


@router.get("/tradingagents/analysis")
def get_tradingagents_analysis(
    stock_symbol: str = Query(..., description="股票代码"),
    analysis_date: str = Query(..., description="分析日期 YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """按 symbol + date 查某次 TradingAgents 深度分析完整结果(详细阅读页用)。"""
    from src.platform.persistence.models import AnalysisHistory

    record = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == "tradingagents",
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == analysis_date,
        )
        .order_by(AnalysisHistory.updated_at.desc(), AnalysisHistory.id.desc())
        .first()
    )
    if not record:
        return None

    return {
        "id": record.id,
        "agent_name": record.agent_name,
        "stock_symbol": record.stock_symbol,
        "analysis_date": record.analysis_date,
        "title": record.title or "",
        "content": record.content,
        "raw_data": record.raw_data or {},
        "created_at": _format_datetime(record.created_at),
        "updated_at": _format_datetime(record.updated_at),
    }


@router.get("/tradingagents/analysis/pdf")
def export_tradingagents_analysis_pdf(
    stock_symbol: str = Query(..., description="股票代码"),
    analysis_date: str = Query(..., description="分析日期 YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """把某次 TradingAgents 深度分析报告导出为 PDF 文件(后台直出,不依赖 Chromium)。

    返回 application/pdf(ResponseWrapperMiddleware 对非 JSON 原样放行,不会包裹)。
    """
    from urllib.parse import quote

    from fastapi.responses import Response

    from src.modules.reporting.pdf_export import assemble_report_markdown, render_analysis_pdf
    from src.platform.persistence.models import AnalysisHistory

    record = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == "tradingagents",
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == analysis_date,
        )
        .order_by(AnalysisHistory.updated_at.desc(), AnalysisHistory.id.desc())
        .first()
    )
    if not record:
        raise api_error(404, "analysis_not_found", "未找到该深度分析记录")

    # 用 raw_data 拼详情页同款完整分节(含 4 分析师全文 + 辩论全文);raw_data 缺失时回退 content
    from src.platform.language import resolve_report_language

    report_language = resolve_report_language(db)
    report_md = assemble_report_markdown(record.raw_data or {}, language=report_language) or (record.content or "")
    english = report_language == "en-US"
    pdf_title = f"{stock_symbol} Deep Analysis" if english else (record.title or "深度分析")
    pdf_bytes = render_analysis_pdf(pdf_title, report_md, language=report_language)
    filename = f"Deep-analysis-{stock_symbol}-{analysis_date}.pdf" if english else f"深度分析-{stock_symbol}-{analysis_date}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/tradingagents/history-comparison")
def get_tradingagents_history_comparison(
    stock_symbol: str = Query(..., description="股票代码,如 300418"),
    market: str = Query("CN", description="市场:CN/US/HK"),
    days: int = Query(90, ge=7, le=365, description="回溯天数"),
):
    """某只股票的 TradingAgents 历史决策 vs 实际涨跌对比。

    返回 items(每条决策 + 1d/5d/20d 后涨跌)+ stats(命中率/平均收益)。
    "命中" 定义:buy→后续上涨 / sell→后续下跌 / hold→|涨跌| < 2%(横盘)。
    """
    from src.modules.automation.tradingagents.operations import build_history_comparison

    return build_history_comparison(stock_symbol=stock_symbol, market=market, days=days)


@router.get("/runs/{trace_id}/progress")
def get_run_progress(trace_id: str, db: Session = Depends(get_db)):
    """读取一次 agent 运行的进度。

    适用 TradingAgents 等长耗时(3-5 分钟)的 agent。从 log_entries 表里
    查 event=ta_progress + 同 trace_id 的日志,聚合成阶段进度。

    返回:
    {
        "trace_id": ...,
        "status": "running" | "success" | "failed" | "not_found",
        "current_stage": ...,
        "completed_stages": [...],
        "elapsed_sec": float,
        "total_cost_usd": float,
        "stages": [{"name": ..., "status": "pending"|"running"|"done"}, ...],
        "run": {  # 最终 AgentRun(已完成时)
            "status": ..., "result": ..., "error": ..., "duration_ms": ...
        }
    }
    """
    from src.modules.automation.tradingagents.observability import aggregate_progress

    if not trace_id or len(trace_id) > 64:
        raise api_error(400, "trace_id_invalid", "无效的 trace_id")

    logs = (
        db.query(LogEntry)
        .filter(
            LogEntry.trace_id == trace_id,
            LogEntry.event.in_(["ta_progress", "ta_toolkit"]),
        )
        .order_by(LogEntry.id.asc())
        .limit(500)
        .all()
    )
    log_dicts = [
        {
            "timestamp": _format_datetime(le.timestamp),
            "level": le.level,
            "message": le.message,
            "event": le.event,
            "tags": le.tags or {},
        }
        for le in logs
    ]

    progress_logs = [d for d in log_dicts if d.get("event") == "ta_progress"]
    progress = aggregate_progress(progress_logs)
    # Progress/tool history is bounded. Cumulative usage must still come from
    # the newest snapshot, including runs with more than 500 log entries.
    if len(logs) >= 500:
        latest_usage = (
            db.query(LogEntry.tags)
            .filter(LogEntry.trace_id == trace_id, LogEntry.event == "ta_progress",
                    LogEntry.tags["token_usage"]["completed_calls"].as_integer().isnot(None))
            .order_by(LogEntry.id.desc())
            .first()
        )
        if latest_usage:
            progress["token_usage"] = latest_usage[0]["token_usage"]

    # 工具调用诊断:汇总 5 类 action 次数 + 最近 50 条详情
    # 港股转格式/兜底等场景归到对应基础类(HIT/PASSTHROUGH/ERROR),
    # source 字段区分具体来源(yfinance/panwatch HK fallback/...)
    toolkit_logs = [d for d in log_dicts if d.get("event") == "ta_toolkit"]
    toolkit_summary = {"hit": 0, "miss": 0, "passthrough": 0, "fallthrough": 0, "error": 0}
    toolkit_recent = []
    for d in toolkit_logs:
        tags = d.get("tags") or {}
        action = (tags.get("action") or "").lower()
        if action in toolkit_summary:
            toolkit_summary[action] += 1
        toolkit_recent.append({
            "timestamp": d.get("timestamp"),
            "action": tags.get("action"),
            "method": tags.get("method"),
            "symbol": tags.get("symbol"),
            "reason": tags.get("reason"),
            "chars": tags.get("chars"),
            "snippet": tags.get("snippet"),
            "source": tags.get("source"),
        })
    progress["toolkit_summary"] = toolkit_summary
    progress["toolkit_recent"] = toolkit_recent[-50:]

    run = (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == trace_id)
        .order_by(AgentRun.id.desc())
        .first()
    )

    if run:
        status = run.status
        progress["run"] = {
            "agent_name": run.agent_name,
            "status": run.status,
            "result": (run.result or "")[:1000],
            "error": (run.error or "")[:2000],
            "duration_ms": run.duration_ms,
            "model_label": run.model_label,
            "notify_sent": run.notify_sent,
        }
        if status == "running":
            created_at = _as_utc(run.created_at)
            if created_at:
                progress["started_at"] = _format_datetime(run.created_at)
                progress["elapsed_sec"] = max(
                    float(progress.get("elapsed_sec") or 0),
                    (datetime.now(timezone.utc) - created_at).total_seconds(),
                )
                if progress["elapsed_sec"] > ACTIVE_RUN_TTL_SEC:
                    status = "stale"
    elif log_dicts:
        # 检测"僵尸 running":server 重启 / 工作线程死掉时,日志还在但任务已不在跑。
        # 最后一条进度日志距今 > STALE_THRESHOLD 视为中断,前端可据此 reset 回 idle。
        STALE_THRESHOLD_SEC = 300  # 5 分钟
        last_log = logs[-1]  # logs 已 order_by id.asc(),末尾是最新
        last_ts = last_log.timestamp
        if last_ts is not None:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            idle_sec = (datetime.now(timezone.utc) - last_ts).total_seconds()
            status = "stale" if idle_sec > STALE_THRESHOLD_SEC else "running"
        else:
            status = "running"
    else:
        status = "not_found"

    progress["trace_id"] = trace_id
    progress["status"] = status
    return progress


# 进度 SSE 轮询/推送节奏与终态判定
PROGRESS_SSE_POLL_SEC = 1.0
PROGRESS_SSE_MAX_DURATION_SEC = 30 * 60
PROGRESS_SSE_NOT_FOUND_GRACE_SEC = 60  # trigger 刚发出时日志可能尚未写入
PROGRESS_TERMINAL_STATUSES = ("success", "failed", "stale")


@router.get("/runs/{trace_id}/progress/stream")
async def stream_run_progress(trace_id: str):
    """进度 SSE：服务端聚合进度，快照有变化即推送（替代前端 2s 轮询）。

    事件分型：
    - progress: 完整进度快照（结构同 GET .../progress），带自增 id；
      快照类事件重连后拿最新一条即可，无需按 Last-Event-ID 严格续推；
    - done: 运行到达终态（success/failed/stale）或 not_found 超过宽限期，随后关流。

    轮询端点 GET .../progress 保留不动，前端 SSE 失败时降级使用。
    """
    import json as _json

    from src.platform.events.sse import format_sse_comment, format_sse_event
    from src.platform.persistence.database import SessionLocal

    if not trace_id or len(trace_id) > 64:
        raise api_error(400, "trace_id_invalid", "无效的 trace_id")

    def _snapshot() -> dict:
        """开独立会话取一次进度快照（复用轮询端点的聚合逻辑）。"""
        db = SessionLocal()
        try:
            return get_run_progress(trace_id, db)
        finally:
            db.close()

    async def gen():
        seq = 0
        last_payload = ""
        started = time.monotonic()
        ticks_since_push = 0
        while time.monotonic() - started < PROGRESS_SSE_MAX_DURATION_SEC:
            try:
                progress = await asyncio.to_thread(_snapshot)
            except Exception as e:
                logger.warning(f"进度 SSE 快照失败: {e}")
                await asyncio.sleep(PROGRESS_SSE_POLL_SEC)
                continue

            payload = _json.dumps(progress, ensure_ascii=False, default=str)
            if payload != last_payload:
                last_payload = payload
                seq += 1
                ticks_since_push = 0
                yield format_sse_event(seq, "progress", payload)
            else:
                ticks_since_push += 1
                if ticks_since_push >= 15:
                    # 无变化时发心跳注释，防止代理断开空闲连接
                    ticks_since_push = 0
                    yield format_sse_comment()

            status = progress.get("status", "")
            not_found_expired = (
                status == "not_found"
                and time.monotonic() - started > PROGRESS_SSE_NOT_FOUND_GRACE_SEC
            )
            if status in PROGRESS_TERMINAL_STATUSES or not_found_expired:
                seq += 1
                yield format_sse_event(seq, "done", {"status": status})
                return

            await asyncio.sleep(PROGRESS_SSE_POLL_SEC)

        # 超时兜底：关流，前端可重连或降级轮询
        seq += 1
        yield format_sse_event(seq, "done", {"status": "timeout"})

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{agent_name}/history", response_model=list[AgentRunResponse])
def get_agent_history(agent_name: str, limit: int = 20, db: Session = Depends(get_db)):
    tz = Settings().app_timezone or "UTC"
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
            trace_id=run.trace_id or "",
            trigger_source=run.trigger_source or "",
            notify_attempted=bool(run.notify_attempted),
            notify_sent=bool(run.notify_sent),
            context_chars=int(run.context_chars or 0),
            model_label=run.model_label or "",
            status=run.status or "",
            result=run.result or "",
            error=run.error or "",
            duration_ms=run.duration_ms or 0,
            created_at=_format_datetime(run.created_at, tz=tz),
        )
        for run in runs
    ]


@router.get("/intraday/snapshot")
async def intraday_snapshot(db: Session = Depends(get_db)):
    """Read-only homepage quotes for configured intraday stocks in open markets.

    This endpoint never calls AI, fetches K-lines, or writes analysis records.
    """
    from server import load_watchlist_for_agent, load_portfolio_for_agent
    from src.platform.marketdata.marketdata_client import md_stock_data
    from src.platform.marketdata.models import MarketCode, MARKETS

    watchlist = await asyncio.to_thread(load_watchlist_for_agent, "intraday_monitor")
    active = [
        stock for stock in watchlist
        if MARKETS.get(stock.market) and MARKETS[stock.market].is_trading_time()
    ]
    if not active:
        return {"stocks": [], "available_funds": 0}
    key = _build_snapshot_cache_key(active)
    cached = _get_snapshot_cache(key)
    if cached is not None:
        return cached

    config = await asyncio.to_thread(
        lambda: db.query(AgentConfig).filter(AgentConfig.name == "intraday_monitor").first()
    )
    threshold = (
        float((config.config or {}).get("price_alert_threshold", 3.0))
        if config else 3.0
    )
    portfolio = await asyncio.to_thread(load_portfolio_for_agent, "intraday_monitor")
    by_market: dict[MarketCode, list[str]] = {}
    for stock in active:
        by_market.setdefault(stock.market, []).append(stock.symbol)

    async def quotes_for_market(market, symbols):
        try:
            quotes = await asyncio.to_thread(md_stock_data, symbols, market.value)
            return [(market, quote) for quote in quotes or []]
        except Exception:
            logger.exception("Intraday snapshot quotes failed: %s", market.value)
            return []

    batches = await asyncio.gather(*(
        quotes_for_market(market, symbols) for market, symbols in by_market.items()
    ))
    rows = []
    for batch in batches:
        for market, quote in batch:
            positions = portfolio.get_positions_for_stock(quote.symbol)
            cost = positions[0].cost_price if positions else None
            change = quote.change_pct or 0
            rows.append({
                "symbol": quote.symbol, "name": quote.name, "market": market.value,
                "current_price": quote.current_price, "change_pct": change,
                "open_price": quote.open_price, "high_price": quote.high_price, "low_price": quote.low_price,
                "volume": quote.volume, "turnover": quote.turnover,
                "alert_type": ("急涨" if change > 0 else "急跌") if abs(change) >= threshold else None,
                "has_position": bool(positions), "cost_price": cost,
                "pnl_pct": (quote.current_price - cost) / cost * 100 if cost and quote.current_price else None,
                "trading_style": positions[0].trading_style if positions else None,
            })
    payload = {"stocks": rows, "available_funds": portfolio.total_available_funds}
    _set_snapshot_cache(key, payload)
    return payload
