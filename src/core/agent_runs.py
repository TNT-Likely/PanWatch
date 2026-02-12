"""Agent 运行记录 - 写入 agent_runs 表（供 UI 查询）"""
import logging

from src.web.database import SessionLocal
from src.web.models import AgentRun

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
