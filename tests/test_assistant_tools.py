"""PanWatch owns business tools; PanAgent only receives generic contracts."""

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pan_agent import ModelMessage, RunRequest
from src.web.database import Base
from src.web.models import Account, Position, Stock  # noqa: F401 - registers metadata


def test_portfolio_tool_is_read_only_and_includes_provenance():
    from src.modules.assistant.tools import build_panwatch_tool_registry

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    account = Account(name="默认账户")
    session.add_all([stock, account])
    session.commit()
    session.add(Position(account_id=account.id, stock_id=stock.id, cost_price=1500, quantity=10))
    session.commit()

    registry = build_panwatch_tool_registry(session)
    result = asyncio.run(registry.execute(
        "get_portfolio",
        RunRequest(run_id="run", messages=[ModelMessage(role="user", content="持仓")]),
        {},
    ))

    assert registry.model_tools()[0].risk == "read"
    assert result.ok is True
    assert "贵州茅台" in result.summary
    assert result.sources[0].name == "PanWatch 持仓"
    session.close()
