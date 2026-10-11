"""Position.quantity 支持小数持仓(DRP/微投产生碎片份额;Ghostfolio 同步写入十进制数量)。"""


def _make_db(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.platform.persistence.models import Account, Position, Stock

    engine = create_engine(f"sqlite:///{tmp_path / 'frac.db'}")
    Account.__table__.create(engine)
    Stock.__table__.create(engine)
    Position.__table__.create(engine)
    return sessionmaker(bind=engine)(), engine


def test_position_quantity_round_trips_float(tmp_path):
    from src.platform.persistence.models import Account, Position, Stock

    db, engine = _make_db(tmp_path)
    try:
        acc = Account(name="frac-test")
        st = Stock(symbol="IVV", name="iShares S&P 500 ETF", market="AU")
        db.add_all([acc, st])
        db.commit()
        db.add(Position(account_id=acc.id, stock_id=st.id, cost_price=98.6, quantity=3.5))
        db.commit()
        db.expire_all()
        qty = db.query(Position).filter_by(account_id=acc.id).one().quantity
        assert qty == 3.5
    finally:
        db.close()
        engine.dispose()


def test_position_quantity_integer_row_reads_as_float(tmp_path):
    from src.platform.persistence.models import Account, Position, Stock

    db, engine = _make_db(tmp_path)
    try:
        acc = Account(name="frac-test")
        st = Stock(symbol="VAS", name="Vanguard Australian Shares ETF", market="AU")
        db.add_all([acc, st])
        db.commit()
        db.add(Position(account_id=acc.id, stock_id=st.id, cost_price=80.0, quantity=10))
        db.commit()
        db.expire_all()
        qty = db.query(Position).filter_by(account_id=acc.id).one().quantity
        assert isinstance(qty, float)
        assert qty == 10.0
    finally:
        db.close()
        engine.dispose()
