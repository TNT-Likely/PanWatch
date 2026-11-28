"""持仓数量放宽到小于 1、最多 8 位小数的回归测试。"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Float, create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base, get_db
from src.platform.persistence.migrations import _m151_fractional_position_quantity
from src.platform.persistence.models import Account, Position, Stock
from src.modules.market.api import stocks
from src.modules.portfolio.api import accounts
from src.modules.portfolio.quantity import (
    format_quantity,
    normalize_quantity,
    validate_position_quantity,
    validate_quantity_precision,
)


# ========== 数量规则 ==========


def test_quantity_rule_accepts_fractional_and_eight_decimal_values():
    """小于 1 的数量与 8 位小数都必须被接受。"""
    assert validate_position_quantity(0.5) == 0.5
    assert validate_position_quantity(0.00000001) == 0.00000001
    assert validate_position_quantity(100) == 100.0


def test_quantity_rule_rejects_non_positive_and_more_than_eight_decimals():
    """非正数与超过 8 位小数的数量必须被拒绝。"""
    for invalid in (0, -1, -0.5, 0.000000001, 1.234567891):
        with pytest.raises(ValueError):
            validate_position_quantity(invalid)


def test_precision_only_rule_keeps_legacy_template_quantities_importable():
    """配置包只校验精度，历史模板里的 0 或负数仍然可以导入。"""
    assert validate_quantity_precision(0) == 0
    assert validate_quantity_precision(-5) == -5
    assert validate_quantity_precision(0.5) == 0.5
    with pytest.raises(ValueError):
        validate_quantity_precision(0.000000001)


def test_quantity_formatters_keep_whole_numbers_clean():
    """整数量保持整数文本，小数量去掉多余的 0。"""
    assert format_quantity(100) == "100"
    assert format_quantity(100.0) == "100"
    assert format_quantity(0.5) == "0.5"
    assert format_quantity(0.00000001) == "0.00000001"
    assert normalize_quantity(100) == 100
    assert normalize_quantity(0.5) == 0.5


def test_position_column_is_declared_as_float():
    """ORM 声明必须是浮点列，否则碎股会被截断。"""
    assert isinstance(Position.__table__.c.quantity.type, Float)


# ========== HTTP 接口 ==========


@pytest.fixture
def api(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app = FastAPI()
    app.include_router(stocks.router, prefix="/stocks")
    app.include_router(accounts.router)
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr(accounts, "get_hkd_cny_rate", lambda: 0.9)
    monkeypatch.setattr(accounts, "get_usd_cny_rate", lambda: 7.0)
    yield TestClient(app), session
    session.close()
    engine.dispose()


def _seed(client, symbol="510300", name="碎股ETF") -> tuple[int, int]:
    stock_id = client.post(
        "/stocks", json={"symbol": symbol, "name": name, "market": "CN", "asset_type": "etf"}
    ).json()["id"]
    account_id = client.post("/accounts", json={"name": "碎股账户"}).json()["id"]
    return stock_id, account_id


def test_position_accepts_quantity_below_one(api):
    """碎股数量可以保存并原样读回。"""
    client, db = api
    stock_id, account_id = _seed(client)

    created = client.post(
        "/positions",
        json={
            "stock_id": stock_id,
            "account_id": account_id,
            "cost_price": 4.123,
            "quantity": 0.5,
        },
    )
    assert created.status_code == 200
    assert created.json()["quantity"] == 0.5

    listed = client.get("/positions").json()
    assert listed[0]["quantity"] == 0.5
    assert db.query(Position).one().quantity == 0.5


def test_position_keeps_eight_decimal_places(api):
    """8 位小数的数量在写入、读取和更新后都保持一致。"""
    client, db = api
    stock_id, account_id = _seed(client)

    created = client.post(
        "/positions",
        json={
            "stock_id": stock_id,
            "account_id": account_id,
            "cost_price": 1.5,
            "quantity": 0.00000001,
        },
    )
    assert created.status_code == 200
    position_id = created.json()["id"]
    assert created.json()["quantity"] == 0.00000001
    assert db.query(Position).one().quantity == 0.00000001

    updated = client.put(f"/positions/{position_id}", json={"quantity": 0.12345678})
    assert updated.status_code == 200
    assert updated.json()["quantity"] == 0.12345678
    assert db.query(Position).one().quantity == 0.12345678


def test_position_rejects_invalid_quantity(api):
    """0、负数与超过 8 位小数的数量必须被接口拒绝。"""
    client, _ = api
    stock_id, account_id = _seed(client)
    payload = {"stock_id": stock_id, "account_id": account_id, "cost_price": 1.0}

    for invalid in (0, -1, 0.000000001):
        response = client.post("/positions", json={**payload, "quantity": invalid})
        assert response.status_code == 422, invalid


def test_portfolio_summary_uses_fractional_quantity(api):
    """组合汇总按碎股数量计算成本，不能被取整。"""
    client, _ = api
    stock_id, account_id = _seed(client)
    client.post(
        "/positions",
        json={
            "stock_id": stock_id,
            "account_id": account_id,
            "cost_price": 4.123,
            "quantity": 0.5,
        },
    )

    summary = client.get(
        "/portfolio/summary?include_quotes=false&refresh_exchange_rates=false"
    ).json()
    position = summary["accounts"][0]["positions"][0]
    assert position["quantity"] == 0.5
    assert summary["total"]["total_cost"] == round(4.123 * 0.5, 2)


# ========== 配置包导入导出 ==========


def test_template_round_trip_keeps_fractional_quantity():
    """配置包导出/导入必须保留碎股数量，不能被取整。"""
    from src.modules.automation.api.templates import TemplatePayload, export_template, import_template

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    source = sessionmaker(bind=engine)()

    stock = Stock(symbol="510300", name="碎股ETF", market="CN", asset_type="etf")
    source.add(stock)
    source.flush()
    account = Account(name="碎股账户", available_funds=1000.0)
    source.add(account)
    source.flush()
    source.add(
        Position(
            account_id=account.id,
            stock_id=stock.id,
            cost_price=4.123,
            quantity=0.5,
        )
    )
    source.commit()

    exported = export_template(
        include_internal=True,
        modules="portfolio",
        db=source,
    )
    assert exported["accounts"][0]["positions"][0]["quantity"] == 0.5

    result = import_template(
        payload=TemplatePayload.model_validate(exported),
        mode="merge",
        modules="portfolio",
        db=source,
    )
    assert result["summary"]["created_positions"] == 0
    assert result["summary"]["updated_positions"] == 1
    assert source.query(Position).one().quantity == 0.5

    source.close()
    engine.dispose()


# ========== 数据迁移 ==========


_LEGACY_SCHEMA = """
CREATE TABLE accounts (
  id INTEGER NOT NULL, name VARCHAR NOT NULL, available_funds FLOAT, enabled BOOLEAN,
  created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id)
);
CREATE TABLE stocks (
  id INTEGER NOT NULL, symbol VARCHAR NOT NULL, name VARCHAR NOT NULL, market VARCHAR NOT NULL,
  asset_type VARCHAR NOT NULL, cost_price FLOAT, quantity INTEGER, invested_amount FLOAT,
  sort_order INTEGER, created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id)
);
CREATE TABLE positions (
  id INTEGER NOT NULL, account_id INTEGER NOT NULL, stock_id INTEGER NOT NULL,
  cost_price FLOAT NOT NULL, quantity INTEGER NOT NULL, invested_amount FLOAT,
  sort_order INTEGER, trading_style VARCHAR, created_at DATETIME, updated_at DATETIME,
  PRIMARY KEY (id),
  CONSTRAINT uq_account_stock UNIQUE (account_id, stock_id),
  FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,
  FOREIGN KEY(stock_id) REFERENCES stocks (id) ON DELETE CASCADE
);
CREATE INDEX ix_positions_account ON positions (account_id);
INSERT INTO accounts VALUES (1, 'acc', 100.0, 1, NULL, NULL);
INSERT INTO stocks VALUES (1, '600519', 'mt', 'CN', 'stock', NULL, NULL, NULL, 0, NULL, NULL);
INSERT INTO stocks VALUES (2, '510300', 'etf', 'CN', 'etf', NULL, NULL, NULL, 0, NULL, NULL);
INSERT INTO stocks VALUES (3, '513100', 'etf2', 'CN', 'etf', NULL, NULL, NULL, 0, NULL, NULL);
INSERT INTO positions VALUES (1, 1, 1, 10.0, 100, NULL, 0, 'swing', NULL, NULL);
INSERT INTO positions VALUES (2, 1, 2, 10.0, 3, NULL, 0, 'swing', NULL, NULL);
"""


def test_migration_rebuilds_quantity_column_and_keeps_rows(tmp_path):
    """旧库升级后 quantity 变成浮点列，持仓数据与索引都要保留。"""
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(_LEGACY_SCHEMA)
    connection.commit()
    connection.close()

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _m151_fractional_position_quantity(conn)

    with engine.connect() as conn:
        columns = {row[1]: row[2] for row in conn.execute(text("PRAGMA table_info(positions)"))}
        assert columns["quantity"] == "FLOAT"
        rows = list(conn.execute(text("SELECT id, quantity FROM positions ORDER BY id")))
        assert rows == [(1, 100.0), (2, 3.0)]
        indexes = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='positions'")
            )
        }
        assert "ix_positions_account" in indexes

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO positions (id, account_id, stock_id, cost_price, quantity) "
                "VALUES (3, 1, 3, 10.0, 0.00000001)"
            )
        )
    with engine.connect() as conn:
        assert list(conn.execute(text("SELECT quantity FROM positions WHERE id = 3")))[0][0] == 0.00000001

    # 迁移可重复执行，不会重复重建或丢数据。
    with engine.begin() as conn:
        _m151_fractional_position_quantity(conn)
    with engine.connect() as conn:
        assert list(conn.execute(text("SELECT COUNT(*) FROM positions")))[0][0] == 3
        columns = {row[1]: row[2] for row in conn.execute(text("PRAGMA table_info(positions)"))}
        assert columns["quantity"] == "FLOAT"
    engine.dispose()


def test_migration_is_noop_on_a_fresh_database(tmp_path):
    """新建库的 quantity 已经是浮点列，迁移不需要重建表。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        columns = {row[1]: row[2] for row in conn.execute(text("PRAGMA table_info(positions)"))}
        assert columns["quantity"] == "FLOAT"

    with engine.begin() as conn:
        _m151_fractional_position_quantity(conn)

    with engine.connect() as conn:
        columns = {row[1]: row[2] for row in conn.execute(text("PRAGMA table_info(positions)"))}
        assert columns["quantity"] == "FLOAT"
        assert list(conn.execute(text("PRAGMA foreign_key_check"))) == []
    engine.dispose()


def test_migration_keeps_the_full_schema_intact(tmp_path):
    """在完整表结构上升级只改 quantity 列类型，其他表和外键不受影响。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'full.db'}")
    Base.metadata.create_all(engine)

    # 把 positions 换回旧的 INTEGER 列，模拟历史库。
    with engine.begin() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'")
        ).scalar()
        legacy_ddl = ddl.replace("quantity FLOAT", "quantity INTEGER")
        assert legacy_ddl != ddl
        conn.execute(text("DROP TABLE positions"))
        conn.execute(text(legacy_ddl))

    with engine.begin() as conn:
        _m151_fractional_position_quantity(conn)

    with engine.connect() as conn:
        columns = {row[1]: row[2] for row in conn.execute(text("PRAGMA table_info(positions)"))}
        assert columns["quantity"] == "FLOAT"
        assert columns["cost_price"] == "FLOAT"
        assert list(conn.execute(text("PRAGMA foreign_key_check"))) == []
    engine.dispose()
