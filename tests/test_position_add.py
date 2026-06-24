"""持仓加仓 API:加权平均成本 + 流水记录。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.web.api import accounts
from src.web.models import Account, Position, PositionTrade, Stock


def _seed_position(db, *, qty=100, cost=10.0):
    acc = Account(name="测试账户", available_funds=100000, enabled=True)
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    db.add(acc)
    db.add(stock)
    db.flush()
    pos = Position(
        account_id=acc.id,
        stock_id=stock.id,
        cost_price=cost,
        quantity=qty,
        invested_amount=cost * qty,
    )
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return pos


def test_calc_weighted_cost_add_to_existing():
    """100@10 加仓 100@8 → 200@9"""
    qty, cost = accounts._calc_weighted_cost(100, 10.0, 100, 8.0)
    assert qty == 200
    assert cost == 9.0


def test_calc_weighted_cost_first_buy():
    """空仓首次买入成本=买入价"""
    qty, cost = accounts._calc_weighted_cost(0, 0, 100, 8.5)
    assert qty == 100
    assert cost == 8.5


def test_add_to_position_updates_cost_and_records_trade(db):
    """加仓接口应更新持仓并写入 position_trades 流水"""
    pos = _seed_position(db, qty=100, cost=10.0)
    res = accounts.add_to_position(
        pos.id,
        accounts.PositionAddRequest(price=8.0, quantity=100),
        db,
    )
    assert res["position"]["quantity"] == 200
    assert res["position"]["cost_price"] == 9.0
    assert res["trade"]["side"] == "buy"
    assert res["trade"]["cost_before"] == 10.0
    assert res["trade"]["cost_after"] == 9.0

    updated = db.query(Position).filter(Position.id == pos.id).first()
    assert updated.quantity == 200
    assert updated.cost_price == 9.0
    assert updated.invested_amount == 1800.0

    trades = db.query(PositionTrade).filter(PositionTrade.position_id == pos.id).all()
    assert len(trades) == 1
    assert trades[0].amount == 800.0


def test_add_to_position_not_found(db):
    """不存在的持仓应 404"""
    with pytest.raises(HTTPException) as exc:
        accounts.add_to_position(
            999999,
            accounts.PositionAddRequest(price=8.0, quantity=100),
            db,
        )
    assert exc.value.status_code == 404


def test_list_position_trades(db):
    """流水列表按时间倒序返回"""
    pos = _seed_position(db)
    accounts.add_to_position(
        pos.id, accounts.PositionAddRequest(price=8.0, quantity=50), db
    )
    accounts.add_to_position(
        pos.id, accounts.PositionAddRequest(price=9.0, quantity=50), db
    )
    rows = accounts.list_position_trades(pos.id, limit=10, db=db)
    assert len(rows) == 2
    assert rows[0]["price"] == 9.0
    assert rows[1]["price"] == 8.0


def test_recent_portfolio_trades(db):
    """全账户最近流水接口应返回 symbol 与账户名"""
    pos = _seed_position(db)
    accounts.add_to_position(
        pos.id, accounts.PositionAddRequest(price=8.0, quantity=50), db
    )
    rows = accounts.recent_portfolio_trades(limit=10, db=db)
    assert len(rows) >= 1
    assert rows[0]["symbol"] == "600519"
    assert rows[0]["account_name"] == "测试账户"
    assert rows[0]["quantity"] == 50


def test_create_position_records_initial_trade(db):
    """新建持仓应写入建仓流水"""
    acc = Account(name="测试账户", available_funds=100000, enabled=True)
    stock = Stock(symbol="000001", name="平安银行", market="CN")
    db.add(acc)
    db.add(stock)
    db.flush()
    res = accounts.create_position(
        accounts.PositionCreate(
            account_id=acc.id,
            stock_id=stock.id,
            cost_price=12.5,
            quantity=500,
        ),
        db=db,
    )
    assert res["quantity"] == 500
    trades = db.query(PositionTrade).filter(PositionTrade.position_id == res["id"]).all()
    assert len(trades) == 1
    assert trades[0].side == "buy"
    assert trades[0].note == "建仓"
    assert trades[0].quantity == 500
    assert trades[0].price == 12.5


def test_reduce_from_position_updates_qty_and_records_trade(db):
    """减仓接口应减少股数、保持成本并写入卖出流水"""
    pos = _seed_position(db, qty=1000, cost=29.63)
    res = accounts.reduce_from_position(
        pos.id,
        accounts.PositionReduceRequest(price=26.5, quantity=300),
        db,
    )
    assert res["position"]["quantity"] == 700
    assert res["position"]["cost_price"] == 29.63
    assert res["trade"]["side"] == "sell"
    assert res["trade"]["qty_after"] == 700
    assert res["trade"]["cost_after"] == 29.63

    updated = db.query(Position).filter(Position.id == pos.id).first()
    assert updated.quantity == 700
    assert updated.cost_price == 29.63


def test_reduce_over_quantity_rejected(db):
    """卖出股数超过持仓应 400"""
    pos = _seed_position(db, qty=100, cost=10.0)
    with pytest.raises(HTTPException) as exc:
        accounts.reduce_from_position(
            pos.id,
            accounts.PositionReduceRequest(price=9.0, quantity=200),
            db,
        )
    assert exc.value.status_code == 400
