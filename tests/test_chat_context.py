"""AI 对话上下文：持仓、今日流水与新闻公告。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from src.collectors.news_collector import NewsItem
from src.core import stock_news_context
from src.web.api import chat
from src.web.models import Account, Position, PositionTrade, Stock


def _make_news_item(*, source: str, title: str, day: int = 25) -> NewsItem:
    return NewsItem(
        source=source,
        external_id=f"{source}-{title}",
        title=title,
        content=f"{title}摘要",
        publish_time=datetime(2026, 6, day, 10, 0),
        symbols=["603596"],
    )


def test_fetch_stock_news_context_splits_news_and_announcements(db):
    """新闻与公告应分开展示"""
    stock = Stock(symbol="603596", name="伯特利", market="CN")
    db.add(stock)
    db.commit()

    items = [
        _make_news_item(source="eastmoney", title="业绩预告"),
        _make_news_item(source="eastmoney_news", title="机构调研"),
        _make_news_item(source="xueqiu", title="行业点评"),
    ]

    with patch("src.collectors.news_collector.NewsCollector.from_database") as mock_from_db:
        collector = AsyncMock()
        collector.fetch_all = AsyncMock(return_value=items)
        mock_from_db.return_value = collector

        ctx = __import__("asyncio").run(
            stock_news_context.fetch_stock_news_context(db, "603596")
        )

    assert "近期公告" in ctx
    assert "业绩预告" in ctx
    assert "近期新闻" in ctx
    assert "机构调研" in ctx
    assert "行业点评" in ctx


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
