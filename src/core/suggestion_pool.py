"""建议池管理 - 汇总各 Agent 建议"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from src.web.database import SessionLocal
from src.web.models import StockSuggestion
from src.core.timezone import utc_now, to_iso_with_tz

logger = logging.getLogger(__name__)

# Agent 有效期配置（小时）
AGENT_EXPIRY_HOURS = {
    "premarket_outlook": 12,    # 盘前建议当日有效（约12小时）
    "intraday_monitor": 4,      # 盘中建议4小时有效
    "daily_report": 16,         # 盘后建议隔夜有效（到次日开盘，约16小时）
}

# Agent 中文名称映射
AGENT_LABELS = {
    "premarket_outlook": "盘前分析",
    "intraday_monitor": "盘中监测",
    "daily_report": "盘后日报",
}


def save_suggestion(
    stock_symbol: str,
    stock_name: str,
    action: str,
    action_label: str,
    agent_name: str,
    signal: str = "",
    reason: str = "",
    agent_label: str = "",
    expires_hours: Optional[int] = None,
    prompt_context: str = "",
    ai_response: str = "",
) -> bool:
    """
    保存 Agent 建议到建议池

    Args:
        stock_symbol: 股票代码
        stock_name: 股票名称
        action: 操作类型 (buy/add/reduce/sell/hold/watch/alert/avoid)
        action_label: 操作中文标签
        agent_name: Agent 名称
        signal: 信号描述
        reason: 建议理由
        agent_label: Agent 中文名称（可选，自动推断）
        expires_hours: 过期时间（小时），不指定则使用默认配置
        prompt_context: Prompt 上下文摘要
        ai_response: AI 原始响应

    Returns:
        是否保存成功
    """
    db = SessionLocal()
    try:
        # 计算过期时间（使用 UTC）
        if expires_hours is None:
            expires_hours = AGENT_EXPIRY_HOURS.get(agent_name, 8)

        now = utc_now()
        expires_at = now + timedelta(hours=expires_hours)

        # Agent 标签
        if not agent_label:
            agent_label = AGENT_LABELS.get(agent_name, agent_name)

        # 创建新建议
        suggestion = StockSuggestion(
            stock_symbol=stock_symbol,
            stock_name=stock_name,
            action=action,
            action_label=action_label,
            signal=signal,
            reason=reason,
            agent_name=agent_name,
            agent_label=agent_label,
            expires_at=expires_at,
            prompt_context=prompt_context[:2000] if prompt_context else "",  # 限制长度
            ai_response=ai_response[:2000] if ai_response else "",  # 限制长度
        )
        db.add(suggestion)
        db.commit()

        logger.info(f"保存建议: {stock_symbol} {action_label} (来源: {agent_label})")
        return True

    except Exception as e:
        logger.error(f"保存建议失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_suggestions_for_stock(
    stock_symbol: str,
    include_expired: bool = False,
    limit: int = 10,
) -> list[dict]:
    """
    获取某只股票的建议列表

    Args:
        stock_symbol: 股票代码
        include_expired: 是否包含已过期建议
        limit: 返回数量限制

    Returns:
        建议列表，按时间倒序
    """
    db = SessionLocal()
    try:
        query = db.query(StockSuggestion).filter(
            StockSuggestion.stock_symbol == stock_symbol
        )

        now = utc_now()
        if not include_expired:
            query = query.filter(
                (StockSuggestion.expires_at == None) |
                (StockSuggestion.expires_at > now)
            )

        suggestions = query.order_by(
            StockSuggestion.created_at.desc()
        ).limit(limit).all()

        return [_to_dict(s, now) for s in suggestions]

    finally:
        db.close()


def get_latest_suggestions(
    stock_symbols: Optional[list[str]] = None,
    include_expired: bool = False,
) -> dict[str, dict]:
    """
    获取所有股票的最新建议（每只股票只返回最新的一条）

    Args:
        stock_symbols: 股票代码列表，None 表示所有
        include_expired: 是否包含已过期建议

    Returns:
        {symbol: suggestion_dict}
    """
    db = SessionLocal()
    try:
        # 使用子查询获取每只股票的最新建议
        from sqlalchemy import func

        # 先获取每只股票最新建议的 ID
        subquery = db.query(
            StockSuggestion.stock_symbol,
            func.max(StockSuggestion.id).label('max_id')
        ).group_by(StockSuggestion.stock_symbol).subquery()

        query = db.query(StockSuggestion).join(
            subquery,
            (StockSuggestion.stock_symbol == subquery.c.stock_symbol) &
            (StockSuggestion.id == subquery.c.max_id)
        )

        if stock_symbols:
            query = query.filter(StockSuggestion.stock_symbol.in_(stock_symbols))

        now = utc_now()
        if not include_expired:
            query = query.filter(
                (StockSuggestion.expires_at == None) |
                (StockSuggestion.expires_at > now)
            )

        suggestions = query.all()

        return {s.stock_symbol: _to_dict(s, now) for s in suggestions}

    finally:
        db.close()


def _to_dict(suggestion: StockSuggestion, now: Optional[datetime] = None) -> dict:
    """将 StockSuggestion 转换为字典（时间使用 ISO 格式带时区）"""
    if now is None:
        now = utc_now()

    is_expired = False
    if suggestion.expires_at:
        # 确保比较时都使用 UTC
        expires_utc = suggestion.expires_at
        if expires_utc.tzinfo is None:
            from src.core.timezone import timezone
            expires_utc = expires_utc.replace(tzinfo=timezone.utc)
        is_expired = expires_utc < now

    # 转换时间为带时区的 ISO 格式
    created_at_str = None
    if suggestion.created_at:
        created_at = suggestion.created_at
        if created_at.tzinfo is None:
            from src.core.timezone import timezone
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at_str = to_iso_with_tz(created_at)

    expires_at_str = None
    if suggestion.expires_at:
        expires_at = suggestion.expires_at
        if expires_at.tzinfo is None:
            from src.core.timezone import timezone
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expires_at_str = to_iso_with_tz(expires_at)

    return {
        "id": suggestion.id,
        "stock_symbol": suggestion.stock_symbol,
        "stock_name": suggestion.stock_name,
        "action": suggestion.action,
        "action_label": suggestion.action_label,
        "signal": suggestion.signal,
        "reason": suggestion.reason,
        "agent_name": suggestion.agent_name,
        "agent_label": suggestion.agent_label,
        "created_at": created_at_str,
        "expires_at": expires_at_str,
        "is_expired": is_expired,
        "prompt_context": suggestion.prompt_context or "",
        "ai_response": suggestion.ai_response or "",
    }


def cleanup_expired_suggestions(days: int = 7) -> int:
    """
    清理过期的建议记录

    Args:
        days: 清理多少天前的记录

    Returns:
        删除的记录数
    """
    db = SessionLocal()
    try:
        cutoff = utc_now() - timedelta(days=days)
        result = db.query(StockSuggestion).filter(
            StockSuggestion.created_at < cutoff
        ).delete()
        db.commit()
        logger.info(f"清理了 {result} 条过期建议")
        return result
    except Exception as e:
        logger.error(f"清理过期建议失败: {e}")
        db.rollback()
        return 0
    finally:
        db.close()
