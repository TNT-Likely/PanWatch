"""Portfolio capability public surface.

Other modules use the factory/service below rather than importing portfolio
repositories or ORM models directly.
"""

from sqlalchemy.orm import Session

from .repository import PortfolioRepository
from .service import PortfolioService


def build_portfolio_service(session: Session) -> PortfolioService:
    """Create the only public portfolio service entry for other modules."""
    return PortfolioService(PortfolioRepository(session))


__all__ = ["PortfolioService", "build_portfolio_service"]
