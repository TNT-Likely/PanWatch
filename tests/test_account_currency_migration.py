from sqlalchemy import create_engine, text

from src.platform.persistence.database import Base, _migrate


def test_existing_account_cash_is_marked_cny_and_new_cash_twd():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT, available_funds REAL)"))
        conn.execute(text("INSERT INTO accounts VALUES (1, 'legacy', 100)"))
    Base.metadata.create_all(engine)
    _migrate(engine)
    with engine.begin() as conn:
        assert conn.execute(text("SELECT cash_currency FROM accounts WHERE id=1")).scalar_one() == "CNY"
        conn.execute(text("INSERT INTO accounts (name, available_funds, cash_currency) VALUES ('new', 100, 'TWD')"))
        assert conn.execute(text("SELECT cash_currency FROM accounts WHERE name='new'")).scalar_one() == "TWD"
    engine.dispose()
