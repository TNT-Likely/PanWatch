import asyncio
from copy import deepcopy
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import AgentConfig, AgentRun, LogEntry
from src.core.schedule_parser import preview_schedule
from src.core.schedule_parser import count_runs_within
from src.config import Settings
from src.core.agent_catalog import (
    AGENT_KIND_CAPABILITY,
    AGENT_KIND_WORKFLOW,
    infer_agent_kind,
)

logger = logging.getLogger(__name__)

_SCAN_CACHE_LOCK = threading.Lock()
_SCAN_CACHE: dict[str, tuple[float, dict]] = {}
_SCAN_CACHE_TTL_SECONDS = {
    False: 12.0,  # quick scan
    True: 25.0,   # AI scan
}


def _build_scan_cache_key(analyze: bool, watchlist) -> str:
    symbols = sorted(f"{s.market.value}:{s.symbol}" for s in watchlist)
    return f"intraday_scan:{int(analyze)}:{'|'.join(symbols)}"


def _get_scan_cache(key: str, analyze: bool) -> dict | None:
    now = time.monotonic()
    ttl = _SCAN_CACHE_TTL_SECONDS[analyze]
    with _SCAN_CACHE_LOCK:
        hit = _SCAN_CACHE.get(key)
        if not hit:
            return None
        ts, payload = hit
        if now - ts > ttl:
            _SCAN_CACHE.pop(key, None)
            return None
        return deepcopy(payload)


def _set_scan_cache(key: str, payload: dict) -> None:
    with _SCAN_CACHE_LOCK:
        _SCAN_CACHE[key] = (time.monotonic(), deepcopy(payload))


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
        "config": agent.config or {},
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
        raise HTTPException(404, f"Agent {agent_name} 不存在")

    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(agent, key, value)

    # capability 仅支持手动调用，不参与调度。
    kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
    if kind == AGENT_KIND_CAPABILITY:
        agent.enabled = False
        agent.schedule = ""

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
        raise HTTPException(404, f"Agent {agent_name} 不存在")
    agent_kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
    if agent_kind == AGENT_KIND_WORKFLOW and not agent.enabled:
        raise HTTPException(400, f"Agent {agent_name} 未启用")

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
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Agent 执行失败: {e}")


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
    1. 查 log_entries 中 event=ta_progress + trace_id 含 -{symbol}- 的最新一条
    2. 看对应 trace_id 在 agent_runs 表是否有完成记录
       - 有完成记录 + status=success → 已完成 (前端可拉 latest 结果显示)
       - 有完成记录 + status=failed → 已失败
       - 无完成记录 + 日志在 30 分钟内 → running
       - 无任何日志 → none

    Returns:
        {"trace_id": str|None, "status": "running"|"success"|"failed"|"none"}
    """
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
        last_ts = latest_log.timestamp
        if last_ts and last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        if last_ts:
            idle_sec = (datetime.now(timezone.utc) - last_ts).total_seconds()
            if idle_sec > 300:  # 5 分钟无新进度 → stale
                status = "stale"

    return {
        "trace_id": trace_id,
        "status": status,
        "last_activity_at": _format_datetime(latest_log.timestamp),
    }


def find_active_tradingagents_trace(db: Session, stock_symbol: str) -> str | None:
    """内部 helper:查该 symbol 是否有"真正在跑"的 tradingagents 任务。

    给 trigger API 做幂等校验用。返回正在跑的 trace_id(或 None)。
    - 有 AgentRun.status 终态(success/failed) → 不在跑(None)
    - 5 分钟无新进度日志 → stale,不在跑(None)
    - 否则 → 返回 trace_id
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
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
        return None

    trace_id = latest_log.trace_id
    run = (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == trace_id)
        .order_by(AgentRun.id.desc())
        .first()
    )
    if run and run.status in ("success", "failed"):
        return None

    last_ts = latest_log.timestamp
    if last_ts and last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    if last_ts and (datetime.now(timezone.utc) - last_ts).total_seconds() > 300:
        return None  # stale → 视为不在跑

    return trace_id


@router.get("/tradingagents/latest")
def get_tradingagents_latest(
    stock_symbol: str = Query(..., description="股票代码,如 300418"),
    db: Session = Depends(get_db),
):
    """获取某只股票最近一次 TradingAgents 深度分析的完整结果(含 raw_data)。

    /history 端点 cherry-pick 字段不含 raw_data,这里专门为深度分析弹窗
    暴露完整字段(suggestion / debate_history / analyst_reports / cost_usd 等)。
    """
    from src.web.models import AnalysisHistory

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
    from src.web.models import AnalysisHistory

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

    from fastapi import HTTPException
    from fastapi.responses import Response

    from src.core.pdf_export import assemble_report_markdown, render_analysis_pdf
    from src.web.models import AnalysisHistory

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
        raise HTTPException(status_code=404, detail="未找到该深度分析记录")

    # 用 raw_data 拼详情页同款完整分节(含 4 分析师全文 + 辩论全文);raw_data 缺失时回退 content
    report_md = assemble_report_markdown(record.raw_data or {}) or (record.content or "")
    pdf_bytes = render_analysis_pdf(record.title or "深度分析", report_md)
    base = (record.title or f"{stock_symbol} 深度分析").replace("/", "-").replace("\\", "-").strip()
    filename = f"{base}-{analysis_date}.pdf"
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
    from src.agents.tradingagents.history_comparison import build_history_comparison

    return build_history_comparison(stock_symbol=stock_symbol, market=market, days=days)


@router.get("/tradingagents/budget")
def get_tradingagents_budget(db: Session = Depends(get_db)):
    """读取 TradingAgents 本月预算使用情况。

    用于 UI 在「设置」+「DeepAnalysisModal」展示「已用 $X / 预算 $Y」。
    """
    agent = (
        db.query(AgentConfig).filter(AgentConfig.name == "tradingagents").first()
    )
    if not agent:
        raise HTTPException(404, "tradingagents agent 未注册")

    cfg = agent.config or {}
    monthly_budget = float(cfg.get("monthly_budget_usd", 10.0))

    # 复用 cost_tracker 的 SQL 聚合
    from src.agents.tradingagents.cost_tracker import check_budget, estimate_cost

    budget = check_budget(monthly_budget, "tradingagents")

    # 单次估算(给前端确认弹窗显示)
    est = estimate_cost(
        debate_rounds=int(cfg.get("debate_rounds", 1)),
        selected_analysts=list(
            cfg.get("analyst_types", ["market", "social", "news", "fundamentals"])
        ),
        model=str(cfg.get("deep_model") or "deepseek-chat"),
    )

    return {
        **budget,
        "estimate_next_run": {
            "cost_low_usd": est["cost_low_usd"],
            "cost_high_usd": est["cost_high_usd"],
            "model": est["model"],
        },
        "over_budget_action": cfg.get("over_budget_action", "reject"),
        "enabled": bool(agent.enabled),
    }


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
    from src.agents.tradingagents.progress import aggregate_progress

    if not trace_id or len(trace_id) > 64:
        raise HTTPException(400, "无效的 trace_id")

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
            "error": (run.error or "")[:500],
            "duration_ms": run.duration_ms,
            "model_label": run.model_label,
            "notify_sent": run.notify_sent,
        }
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
    from src.core.context_builder import ContextBuilder
    from src.core.signals import SignalPackBuilder
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

    # 按股票所属市场过滤：只扫描当前开市市场的股票（避免全局门禁误判）
    active_watchlist = [
        s for s in watchlist if MARKETS.get(s.market) and MARKETS[s.market].is_trading_time()
    ]
    if not active_watchlist:
        return {
            "stocks": [],
            "message": "当前非交易时段",
            "scanned_count": len(watchlist),
            "total_watchlist_count": len(watchlist),
            "skipped_not_trading_count": len(watchlist),
            "is_trading": False,
            "has_watchlist": True,
        }

    cache_key = _build_scan_cache_key(analyze, active_watchlist)
    cached = _get_scan_cache(cache_key, analyze)
    if cached is not None:
        return cached

    # 获取持仓信息
    portfolio = load_portfolio_for_agent(agent_name)

    # 按市场分组采集行情
    market_symbols: dict[MarketCode, list] = {}
    stock_market_map: dict[str, MarketCode] = {}
    for stock in active_watchlist:
        market_symbols.setdefault(stock.market, []).append(stock.symbol)
        stock_market_map[stock.symbol] = stock.market

    async def _fetch_market_quotes(market_code: MarketCode, symbols: list[str]):
        try:
            collector = AkshareCollector(market_code)
            return await collector.get_stock_data(symbols)
        except Exception as e:
            logger.error(f"采集 {market_code.value} 行情失败: {e}")
            return []

    quote_batches = await asyncio.gather(
        *[
            _fetch_market_quotes(market_code, symbols)
            for market_code, symbols in market_symbols.items()
        ]
    )
    all_quotes = [q for batch in quote_batches for q in (batch or [])]
    quote_by_symbol = {q.symbol: q for q in all_quotes}

    # 解析 Agent 阈值配置（用于异动标记与提示 AI）
    try:
        monitor_agent = IntradayMonitorAgent(bypass_throttle=True, **agent_kwargs)
    except TypeError:
        # 兼容旧配置（字段不匹配时回退）
        monitor_agent = IntradayMonitorAgent(bypass_throttle=True)

    daily_analysis = None
    premarket_analysis = None
    scan_context = None
    symbol_contexts: dict[str, dict] = {}
    quality_overview: dict = {}
    signal_packs: dict = {}
    if analyze:
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

        try:
            scan_context = build_context(agent_name)
            original_watchlist = scan_context.config.watchlist
            scan_context.config.watchlist = active_watchlist
            sym_list = [(s.symbol, s.market, s.name) for s in active_watchlist]
            signal_packs = await SignalPackBuilder().build_for_symbols(
                symbols=sym_list,
                include_news=True,
                news_hours=24,
                portfolio=portfolio,
                include_technical=True,
                include_capital_flow=True,
                include_events=True,
                events_days=3,
            )
            context_pack = await ContextBuilder().build_symbol_contexts(
                agent_name=agent_name,
                context=scan_context,
                packs=signal_packs,
                realtime_hours=6,
                extended_hours=24,
                history_days=7,
                kline_days=60,
                persist_snapshot=False,
            )
            symbol_contexts = context_pack.get("symbols", {}) or {}
            quality_overview = context_pack.get("quality_overview", {}) or {}
        except Exception as e:
            logger.warning(f"构建盘中扫描上下文失败，回退基础分析: {e}")
        finally:
            try:
                if scan_context:
                    scan_context.config.watchlist = original_watchlist
            except Exception:
                pass

    # 构建返回数据
    kline_sem = asyncio.Semaphore(6)

    async def _load_kline_summary(symbol: str, market: MarketCode):
        try:
            async with kline_sem:
                return await asyncio.to_thread(
                    lambda: KlineCollector(market).get_kline_summary(symbol)
                )
        except Exception as e:
            logger.warning(f"获取 {symbol} K线失败: {e}")
            return None

    async def _build_result_item(quote):
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

        # 获取技术分析（并发）
        kline_summary = await _load_kline_summary(quote.symbol, market)

        # 判断异动类型
        alert_type = None
        if abs(change_pct) >= getattr(monitor_agent, "price_alert_threshold", 3.0):
            alert_type = "急涨" if change_pct > 0 else "急跌"

        return {
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
            "context_quality": (
                (symbol_contexts.get(quote.symbol, {}) or {}).get("data_quality")
                if analyze
                else None
            ),
        }

    results = await asyncio.gather(*[_build_result_item(quote) for quote in all_quotes])

    # AI 分析
    if analyze and results:
        try:
            context = scan_context or build_context(agent_name)
            agent = monitor_agent

            ai_sem = asyncio.Semaphore(3)

            async def _analyze_item(item: dict):
                try:
                    async with ai_sem:
                        stock_data = quote_by_symbol.get(item["symbol"])
                        if not stock_data:
                            return

                        data = {
                            "stock_data": stock_data,
                            "stocks": [stock_data],
                            "kline_summary": (
                                (signal_packs.get(item["symbol"]).technical)
                                if signal_packs.get(item["symbol"])
                                else item["kline"]
                            ),
                            "signal_pack": signal_packs.get(item["symbol"]),
                            "symbol_context": symbol_contexts.get(item["symbol"], {}),
                            "quality_overview": quality_overview,
                            "daily_analysis": daily_analysis.content
                            if daily_analysis
                            else None,
                            "premarket_analysis": premarket_analysis.content
                            if premarket_analysis
                            else None,
                        }

                        # 事件门禁仅保留为上下文信息，不阻断 AI 分析。
                        # 产品策略：建议持续更新，通知层再做去重与降噪。
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
                                data["event_gate"] = {
                                    "reasons": decision.reasons,
                                    "should_analyze": bool(decision.should_analyze),
                                }
                        except Exception:
                            pass

                        system_prompt, user_content = agent.build_prompt(data, context)
                        response = await context.ai_client.chat(
                            system_prompt, user_content
                        )

                        # 解析结构化建议
                        suggestion = agent._parse_suggestion(response)
                        suggestion["raw"] = response.strip()[:200]

                        item["suggestion"] = suggestion
                        # 写入建议池（用于持仓页展示），盘中建议固定 6 小时有效
                        expires_hours = 6
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
                            stock_market=item.get("market") or "CN",
                            meta={
                                "source": "intraday_scan",
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
                                "context_quality_score": (
                                    (data.get("symbol_context") or {})
                                    .get("data_quality", {})
                                    .get("score")
                                ),
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

            await asyncio.gather(*[_analyze_item(item) for item in results])

        except Exception as e:
            logger.error(f"构建 Agent 上下文失败: {e}")

    payload = {
        "stocks": results,
        "scanned_count": len(active_watchlist),
        "total_watchlist_count": len(watchlist),
        "skipped_not_trading_count": len(watchlist) - len(active_watchlist),
        "is_trading": True,
        "has_watchlist": True,
        "available_funds": portfolio.total_available_funds,
        "quality_overview": quality_overview if analyze else {},
    }
    _set_scan_cache(cache_key, payload)
    return payload
