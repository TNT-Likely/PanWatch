"""建议池 API"""
import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.web.database import get_db
from src.core.suggestion_pool import (
    get_suggestions_for_stock,
    get_latest_suggestions,
    cleanup_expired_suggestions,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{symbol}")
def get_stock_suggestions(
    symbol: str,
    include_expired: bool = Query(False, description="是否包含已过期建议"),
    limit: int = Query(10, description="返回数量限制"),
    db: Session = Depends(get_db),
):
    """
    获取某只股票的所有建议

    返回该股票的建议列表，按时间倒序排列
    """
    suggestions = get_suggestions_for_stock(
        stock_symbol=symbol,
        include_expired=include_expired,
        limit=limit,
    )
    return suggestions


@router.get("/", name="get_suggestions")
@router.get("", include_in_schema=False)  # 同时处理无斜杠的情况
def get_all_latest_suggestions(
    symbols: str = Query(None, description="股票代码列表，逗号分隔"),
    include_expired: bool = Query(False, description="是否包含已过期建议"),
    db: Session = Depends(get_db),
):
    """
    获取所有股票的最新建议

    每只股票只返回最新的一条有效建议
    用于持仓页面快速展示各股票的最新建议
    """
    symbol_list = None
    if symbols:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]

    suggestions = get_latest_suggestions(
        stock_symbols=symbol_list,
        include_expired=include_expired,
    )
    return suggestions


@router.delete("/cleanup")
def cleanup_suggestions(
    days: int = Query(7, description="清理多少天前的记录"),
    db: Session = Depends(get_db),
):
    """
    清理过期的建议记录

    默认清理 7 天前的记录
    """
    count = cleanup_expired_suggestions(days=days)
    return {"deleted": count}
