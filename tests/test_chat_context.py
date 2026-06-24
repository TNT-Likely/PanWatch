"""AI 对话上下文：持仓与今日流水。"""

from __future__ import annotations

from datetime import datetime, timezone

from src.web.api import chat
from src.web.models import Account, Position, PositionTrade, Stock


def _seed_trade(db, *, side="buy", qty=300, price=26.0):
    acc = Account(name="测试账户", available_funds=100000, enabled=True)
    stock = Stock(symbol="603596", name="伯特利", market="CN")
    db.add(acc)
    db.add(stock)
    db.flush()
    pos = Position(
        account_id=acc.id,
        stock_id=stock.id,
        cost_price=29.63,
        quantity=2700,
        invested_amount=29.63 * 2700,
    )
    db.add(pos)
    db.flush()
    trade = PositionTrade(
        position_id=pos.id,
        side=side,
        price=price,
        quantity=qty,
        amount=price * qty,
        cost_before=29.63,
        qty_before=2700 if side == "sell" else 2400,
        cost_after=29.63,
        qty_after=(2700 - qty) if side == "sell" else 2700,
        traded_at=datetime.now(timezone.utc).replace(tzinfo=None),
        note="测试流水",
    )
    db.add(trade)
    db.commit()
    return stock, trade


def test_build_stock_position_context_includes_weighted_cost(db):
    """单股持仓摘要应返回最新股数与加权成本"""
    _seed_trade(db)
    ctx = chat._build_stock_position_context(db, "603596", "CN")
    assert "603596" in ctx
    assert "2700股" in ctx
    assert "29.6300" in ctx


def test_build_recent_trades_context_filters_today(db):
    """今日流水上下文应包含买卖记录"""
    stock, _trade = _seed_trade(db, side="sell", qty=400, price=26.44)
    ctx = chat._build_recent_trades_context(
        db, symbol=stock.symbol, market=stock.market, today_only=True
    )
    assert "今日" in ctx
    assert "卖出" in ctx
    assert "603596" in ctx
    assert "400股" in ctx
