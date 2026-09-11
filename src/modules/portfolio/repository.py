"""Portfolio persistence queries used by module services."""

from __future__ import annotations

from sqlalchemy.orm import Session

# 表由共享持久化平台注册；组合 repository 直接使用它们，避免模块内保留
# 一个不承载任何领域行为的 ``portfolio.models`` re-export 文件。
from src.platform.persistence.models import PaperTradingPosition, Position, Stock


class PortfolioRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_real_positions_with_stocks(self) -> list[tuple[Position, Stock]]:
        return (
            self._session.query(Position, Stock)
            .join(Stock, Position.stock_id == Stock.id)
            .order_by(Stock.sort_order.asc(), Position.id.asc())
            .all()
        )

    def list_open_paper_positions(self) -> list[PaperTradingPosition]:
        return (
            self._session.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
