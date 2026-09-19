"""Agent 运行记录 - 写入 agent_runs 表（供 UI 查询）"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AgentRun, LogEntry

logger = logging.getLogger(__name__)

# 采集阶段可能在外部数据源限流/重试时暂时没有进度日志，不能沿用
# “5 分钟无日志即 stale”的规则；但服务重启后也不能无限恢复旧任务。
ACTIVE_RUN_TTL_SEC = 45 * 60


def _as_utc(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def start_agent_run(
    agent_name: str,
    trace_id: str,
    trigger_source: str = "",
    model_label: str = "",
) -> None:
    """在任务真正开始前写入 running 生命周期记录。

    同一 trace 可能同时从 API 包装器和执行入口调用，因此写入是幂等的。
    """
    if not trace_id:
        return
    db = SessionLocal()
    try:
        existing = (
            db.query(AgentRun)
            .filter(AgentRun.trace_id == trace_id, AgentRun.status == "running")
            .order_by(AgentRun.id.desc())
            .first()
        )
        if existing:
            return
        db.add(AgentRun(
            agent_name=agent_name,
            status="running",
            trace_id=trace_id[:64],
            trigger_source=(trigger_source or "")[:32],
            model_label=(model_label or "")[:255],
        ))
        db.commit()
    except Exception as e:
        logger.warning(f"写入 AgentRun running 状态失败: {e}")
        db.rollback()
    finally:
        db.close()


def record_agent_run(
    agent_name: str,
    status: str,
    result: str = "",
    error: str = "",
    duration_ms: int = 0,
    trace_id: str = "",
    trigger_source: str = "",
    notify_attempted: bool = False,
    notify_sent: bool = False,
    context_chars: int = 0,
    model_label: str = "",
) -> None:
    """记录一次 Agent 运行结果到数据库。

    Args:
        agent_name: Agent 名称
        status: success / failed
        result: 简要结果（会截断）
        error: 错误信息（会截断）
        duration_ms: 执行耗时（毫秒）
        trace_id: 运行链路追踪 id
        trigger_source: schedule / manual / api
        notify_attempted: 是否尝试发送通知
        notify_sent: 通知是否发送成功
        context_chars: prompt/context 字符数
        model_label: 本次运行使用的模型标识
    """
    db = SessionLocal()
    try:
        existing = None
        if trace_id:
            existing = (
                db.query(AgentRun)
                .filter(AgentRun.trace_id == trace_id, AgentRun.status == "running")
                .order_by(AgentRun.id.desc())
                .first()
            )
        values = {
            "agent_name": agent_name,
            "status": status,
            "trace_id": (trace_id or "")[:64],
            "trigger_source": (trigger_source or "")[:32],
            "notify_attempted": bool(notify_attempted),
            "notify_sent": bool(notify_sent),
            "context_chars": max(0, int(context_chars or 0)),
            "model_label": (model_label or "")[:255],
            "result": (result or "")[:2000],
            "error": (error or "")[:2000],
            "duration_ms": duration_ms,
        }
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            db.add(AgentRun(**values))
        db.commit()
    except Exception as e:
        logger.warning(f"写入 AgentRun 失败: {e}")
        db.rollback()
    finally:
        db.close()


def find_active_tradingagents_trace(db: Session, stock_symbol: str) -> str | None:
    """返回标的仍在执行的 TradingAgents trace，用于跨模块幂等触发。

    运行状态属于自动化模块，市场模块只能通过这个公开查询判断是否需要创建新任务，
    不应导入自动化 HTTP router 或直接查询其内部实现。
    """
    now = datetime.now(timezone.utc)

    # 生命周期记录是首选数据源：采集阶段还没有 ta_progress 时也能恢复，
    # 且不会因为某个外部源 5 分钟没有日志就重复触发任务。
    active_run = (
        db.query(AgentRun)
        .filter(
            AgentRun.agent_name == "tradingagents",
            AgentRun.status == "running",
            AgentRun.trace_id.like(f"%-{stock_symbol}-%"),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .first()
    )
    if active_run and active_run.trace_id:
        created_at = _as_utc(active_run.created_at)
        if created_at is None or (now - created_at).total_seconds() <= ACTIVE_RUN_TTL_SEC:
            return active_run.trace_id
        # 已超过整个任务安全窗口时，不能再被旧日志重新判成 running。
        return None

    cutoff = now - timedelta(minutes=30)
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

    last_ts = _as_utc(latest_log.timestamp)
    if last_ts and (now - last_ts).total_seconds() > 300:
        return None
    return trace_id
