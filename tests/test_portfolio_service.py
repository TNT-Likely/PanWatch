"""Portfolio module publishes read models without HTTP-router coupling."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import Account, Position, Stock  # noqa: F401 - registers metadata


def test_portfolio_service_builds_position_summary_from_repository():
    from src.modules.portfolio.repository import PortfolioRepository
    from src.modules.portfolio.service import PortfolioService

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    account = Account(name="默认账户")
    session.add_all([stock, account])
    session.commit()
    session.add(Position(account_id=account.id, stock_id=stock.id, cost_price=1500, quantity=100, trading_style="swing"))
    session.commit()

    result = PortfolioService(PortfolioRepository(session)).build_assistant_summary()

    assert "贵州茅台(CN:600519) 100股 成本1500.0 风格swing" in result
    session.close()
