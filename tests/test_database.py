from sqlalchemy import create_engine, text

from src.platform.persistence import database
from src.platform.persistence.database import _backfill_sort_order, _is_sqlite_lock_error


def test_backfill_sort_order_only_writes_when_rows_need_initialization():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE stocks ("
                "id INTEGER PRIMARY KEY, sort_order INTEGER"
                ")"
            )
        )
        conn.execute(text("INSERT INTO stocks (id, sort_order) VALUES (1, 10)"))

    with engine.connect() as conn:
        _backfill_sort_order(conn, "stocks")
        assert conn.execute(text("SELECT sort_order FROM stocks WHERE id = 1")).scalar() == 10

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO stocks (id, sort_order) VALUES (2, 0)"))

    with engine.connect() as conn:
        _backfill_sort_order(conn, "stocks")
        assert conn.execute(text("SELECT sort_order FROM stocks WHERE id = 2")).scalar() == 2


def test_is_sqlite_lock_error_checks_wrapped_exception():
    cause = RuntimeError("database is locked")
    wrapped = RuntimeError("outer error")
    wrapped.__cause__ = cause

    assert _is_sqlite_lock_error(wrapped)
    assert not _is_sqlite_lock_error(RuntimeError("connection failed"))


def test_init_db_retries_transient_sqlite_lock(monkeypatch):
    calls = 0
    sleeps = []

    def flaky_init():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("database is locked")

    monkeypatch.setattr(database, "_init_db_once", flaky_init)
    monkeypatch.setattr(database.time, "sleep", sleeps.append)

    database.init_db()

    assert calls == 3
    assert sleeps == [0.5, 1.0]
