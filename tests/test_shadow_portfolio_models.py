from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.platform.persistence.database import Base
from src.platform.persistence.models import Account, Position, ShadowPosition, ShadowTrade, Stock


def test_shadow_trade_keeps_real_position_and_decision_audit_fields():
    """影子仓位与成交记录可追溯真实持仓和后续决策来源。"""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        account = Account(name="实盘")
        stock = Stock(symbol="600226", market="CN", name="亨通股份")
        session.add_all([account, stock])
        session.flush()
        real = Position(account_id=account.id, stock_id=stock.id, cost_price=9.057, quantity=157100)
        session.add(real)
        session.flush()
        shadow = ShadowPosition(account_id=account.id, stock_id=stock.id, real_position_id=real.id, stock_symbol=stock.symbol, stock_market=stock.market, stock_name=stock.name, initial_quantity=real.quantity, quantity=real.quantity, avg_cost=real.cost_price)
        session.add(shadow)
        session.flush()
        trade = ShadowTrade(shadow_position_id=shadow.id, action="reduce", quantity=117825, target_weight_pct=25, fill={"commission": 12.5, "slippage": 3.0})
        session.add(trade)
        session.commit()
        saved = session.query(ShadowTrade).one()
        assert saved.shadow_position.real_position_id == real.id
        assert saved.fill["commission"] == 12.5
        assert saved.target_weight_pct == 25
    finally:
        session.close()
        engine.dispose()
