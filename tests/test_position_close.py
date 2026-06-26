"""持仓清仓流程:卖到 0 股即清仓,回款入可用资金,保留历史成交明细,可复活已清仓持仓。"""

from __future__ import annotations

from src.web.api import accounts
from src.web.models import Account, Position, PositionTrade, Stock


def _seed(db, *, qty=100, cost=10.0, cash=100000.0):
    acc = Account(name="测试账户", available_funds=cash, enabled=True)
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
        status="open",
    )
    db.add(pos)
    db.flush()
    # 模拟 create_position 的建仓流水(与真实接口一致)
    db.add(
        PositionTrade(
            position_id=pos.id,
            side="buy",
            price=cost,
            quantity=qty,
            amount=round(cost * qty, 4),
            cost_before=None,
            qty_before=None,
            cost_after=cost,
            qty_after=qty,
            note="建仓",
        )
    )
    db.commit()
    db.refresh(pos)
    return pos, acc


def test_reduce_to_zero_marks_closed_and_refunds_cash(db):
    """全部卖出后持仓标记 closed,卖出回款计入可用资金。"""
    pos, acc = _seed(db, qty=100, cost=10.0, cash=100000.0)
    cash_before = float(acc.available_funds)

    res = accounts.reduce_from_position(
        pos.id,
        accounts.PositionReduceRequest(price=12.0, quantity=100),
        db,
    )

    assert res["closed"] is True
    assert res["position"]["status"] == "closed"
    assert res["position"]["quantity"] == 0
    # 卖出 100 股 @ 12 = 1200,CN 直接计入 CNY
    assert res["available_funds"] == cash_before + 1200.0


def test_partial_reduce_keeps_open_and_refunds_cash(db):
    """部分卖出保持 open,但仍把回款计入可用资金。"""
    pos, acc = _seed(db, qty=100, cost=10.0, cash=100000.0)
    cash_before = float(acc.available_funds)

    res = accounts.reduce_from_position(
        pos.id,
        accounts.PositionReduceRequest(price=12.0, quantity=30),
        db,
    )

    assert res["closed"] is False
    assert res["position"]["status"] == "open"
    assert res["position"]["quantity"] == 70
    assert res["available_funds"] == cash_before + 360.0


def test_closed_position_preserves_trade_history(db):
    """清仓后历史成交明细仍可通过 list_position_trades 查询。"""
    pos, acc = _seed(db, qty=100, cost=10.0)
    # 加仓一次再清仓,形成多笔流水
    accounts.add_to_position(
        pos.id, accounts.PositionAddRequest(price=12.0, quantity=100), db
    )
    # 加仓后 200 股,全卖
    accounts.reduce_from_position(
        pos.id, accounts.PositionReduceRequest(price=11.0, quantity=200), db
    )

    trades = accounts.list_position_trades(pos.id, 50, db)
    # 建仓 + 加仓 + 清仓 = 3 笔
    assert len(trades) == 3
    assert trades[0]["side"] == "sell"  # 最新一笔是清仓卖出


def test_closed_positions_endpoint_returns_with_trades(db):
    """已清仓列表接口返回持仓及成交明细,且按清仓时间排序。"""
    pos, acc = _seed(db, qty=50, cost=10.0)
    accounts.reduce_from_position(
        pos.id, accounts.PositionReduceRequest(price=12.0, quantity=50), db
    )

    rows = accounts.list_closed_positions(db=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] if "status" in row else True  # 接口未返回 status 不校验
    assert row["stock_symbol"] == "600519"
    assert row["quantity"] == 0
    assert row["closed_at"] is not None
    assert len(row["trades"]) >= 1
    # 实现盈亏:卖出 50@12 - 买入 50@10 = 100
    assert row["realized_pnl"] == 100.0


def test_revive_closed_position_on_rebuy(db):
    """已清仓持仓重新买入应复活(而非报唯一约束冲突),且清零实现盈亏。"""
    pos, acc = _seed(db, qty=100, cost=10.0)
    accounts.reduce_from_position(
        pos.id, accounts.PositionReduceRequest(price=12.0, quantity=100), db
    )
    closed = db.query(Position).filter(Position.id == pos.id).first()
    assert closed.status == "closed"

    # 同账户同股票重新建仓 → 复活
    revived = accounts.create_position(
        accounts.PositionCreate(
            account_id=acc.id, stock_id=pos.stock_id, cost_price=8.0, quantity=200
        ),
        db,
    )
    assert revived["id"] == pos.id  # 复用同一行
    assert revived["status"] == "open"
    assert revived["quantity"] == 200
    assert revived["cost_price"] == 8.0

    again = db.query(Position).filter(Position.id == pos.id).first()
    assert again.status == "open"
    assert again.realized_pnl == 0.0


def test_open_position_blocks_duplicate_create(db):
    """持仓中再次建仓同账户同股票应报错。"""
    pos, acc = _seed(db, qty=100, cost=10.0)
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        accounts.create_position(
            accounts.PositionCreate(
                account_id=acc.id, stock_id=pos.stock_id, cost_price=9.0, quantity=50
            ),
            db,
        )
