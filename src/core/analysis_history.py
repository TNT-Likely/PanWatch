"""分析历史记录管理"""
import logging
from datetime import date

from src.core.agent_catalog import infer_agent_kind
from src.web.database import SessionLocal
from src.web.models import AnalysisHistory
from src.core.json_safe import to_jsonable

logger = logging.getLogger(__name__)


def save_analysis(
    agent_name: str,
    stock_symbol: str,
    content: str,
    title: str = "",
    raw_data: dict | None = None,
    analysis_date: date | None = None,
) -> bool:
    """
    保存分析结果

    - 同一天可以覆盖
    - 历史记录不可覆盖（通过数据库约束保证）

    Args:
        agent_name: Agent 名称，如 "daily_report"
        stock_symbol: 股票代码，"*" 表示全局分析
        content: AI 分析内容
        title: 分析标题
        raw_data: 原始数据快照
        analysis_date: 分析日期，默认今天

    Returns:
        是否保存成功
    """
    if analysis_date is None:
        analysis_date = date.today()

    date_str = analysis_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        payload = to_jsonable(raw_data or {})
        agent_kind = infer_agent_kind(agent_name)

        # 查找是否已存在
        existing = db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == date_str,
        ).first()

        if existing:
            # 更新（同一天可覆盖）
            existing.title = title
            existing.content = content
            existing.raw_data = payload
            existing.agent_kind_snapshot = agent_kind
            logger.info(f"更新分析记录: {agent_name}/{stock_symbol}/{date_str}")
        else:
            # 新增
            record = AnalysisHistory(
                agent_name=agent_name,
                stock_symbol=stock_symbol,
                analysis_date=date_str,
                title=title,
                content=content,
                raw_data=payload,
                agent_kind_snapshot=agent_kind,
            )
            db.add(record)
            logger.info(f"新增分析记录: {agent_name}/{stock_symbol}/{date_str}")

        db.commit()
        return True

    except Exception as e:
        logger.error(f"保存分析记录失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_analysis(
    agent_name: str,
    stock_symbol: str,
    analysis_date: date | None = None,
) -> AnalysisHistory | None:
    """
    获取分析结果

    Args:
        agent_name: Agent 名称
        stock_symbol: 股票代码
        analysis_date: 分析日期，默认今天

    Returns:
        分析记录，或 None
    """
    if analysis_date is None:
        analysis_date = date.today()

    date_str = analysis_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        return db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == date_str,
        ).first()
    finally:
        db.close()


def get_latest_analysis(
    agent_name: str,
    stock_symbol: str,
    before_date: date | None = None,
) -> AnalysisHistory | None:
    """
    获取最近的分析结果（用于获取昨日/历史分析）

    Args:
        agent_name: Agent 名称
        stock_symbol: 股票代码
        before_date: 在此日期之前的最近记录，默认今天

    Returns:
        分析记录，或 None
    """
    if before_date is None:
        before_date = date.today()

    date_str = before_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        return db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date < date_str,
        ).order_by(AnalysisHistory.analysis_date.desc()).first()
    finally:
        db.close()


def get_analysis_history(
    agent_name: str,
    stock_symbol: str | None = None,
    limit: int = 30,
) -> list[AnalysisHistory]:
    """
    获取分析历史列表

    Args:
        agent_name: Agent 名称
        stock_symbol: 股票代码，None 表示所有
        limit: 返回数量限制

    Returns:
        分析记录列表，按日期倒序
    """
    db = SessionLocal()
    try:
        query = db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
        )

        if stock_symbol:
            query = query.filter(AnalysisHistory.stock_symbol == stock_symbol)

        return query.order_by(AnalysisHistory.analysis_date.desc()).limit(limit).all()
    finally:
        db.close()
