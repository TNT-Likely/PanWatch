"""Agent 运行记录 - 写入 agent_runs 表（供 UI 查询）"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AgentRun, LogEntry

logger = logging.getLogger(__name__)


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
        db.add(AgentRun(
            agent_name=agent_name,
            status=status,
            trace_id=(trace_id or "")[:64],
            trigger_source=(trigger_source or "")[:32],
            notify_attempted=bool(notify_attempted),
            notify_sent=bool(notify_sent),
            context_chars=max(0, int(context_chars or 0)),
            model_label=(model_label or "")[:255],
            result=(result or "")[:2000],
            error=(error or "")[:2000],
            duration_ms=duration_ms,
        ))
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
        return None
    return trace_id
