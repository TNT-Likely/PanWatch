"""security_type 字段与迁移回填测试。"""

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from src.web import models  # noqa: F401  注册 ORM 模型
from src.web.database import Base
from src.web.migrations import _m123_stock_security_type


def _fresh_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


def _insert_stock(conn, symbol, market, name="x"):
    conn.execute(
        text(
            "INSERT INTO stocks (symbol, name, market, sort_order) "
            "VALUES (:s, :n, :m, 0)"
        ),
        {"s": symbol, "n": name, "m": market},
    )


def _security_type(conn, symbol) -> str:
    row = conn.execute(
        text("SELECT security_type FROM stocks WHERE symbol = :s"), {"s": symbol}
    ).fetchone()
    return row[0] if row else None


def test_migration_backfills_cn_etf_codes():
    """CN 市场以 5/15 开头的代码回填为 etf。"""
    eng = _fresh_engine()
    with eng.begin() as conn:
        _insert_stock(conn, "510300", "CN", "沪深300ETF")
        _insert_stock(conn, "159915", "CN", "创业板ETF")
        _insert_stock(conn, "600519", "CN", "贵州茅台")
        _insert_stock(conn, "000001", "CN", "平安银行")
        _m123_stock_security_type(conn)
        assert _security_type(conn, "510300") == "etf"
        assert _security_type(conn, "159915") == "etf"
        assert _security_type(conn, "600519") == "stock"
        assert _security_type(conn, "000001") == "stock"


def test_migration_leaves_non_cn_stocks_untouched():
    """HK/US 代码不受 ETF 回填影响(保持 stock)。"""
    eng = _fresh_engine()
    with eng.begin() as conn:
        _insert_stock(conn, "00700", "HK", "腾讯")
        _insert_stock(conn, "AAPL", "US", "Apple")
        _m123_stock_security_type(conn)
        assert _security_type(conn, "00700") == "stock"
        assert _security_type(conn, "AAPL") == "stock"


def test_migration_idempotent():
    """重复执行不报错、不改变结果。"""
    eng = _fresh_engine()
    with eng.begin() as conn:
        _insert_stock(conn, "510300", "CN", "沪深300ETF")
        _m123_stock_security_type(conn)
        _m123_stock_security_type(conn)  # 不应抛错
        assert _security_type(conn, "510300") == "etf"
