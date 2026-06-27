"""AI 对话会话 API。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.web.api.chat import list_recent_conversations
from src.web.models import ChatConversation


def _seed_conv(db, *, symbol=None, market=None, title="", updated_at=None):
    conv = ChatConversation(
        title=title,
        stock_symbol=symbol,
        stock_market=market,
    )
    db.add(conv)
    db.flush()
    if updated_at is not None:
        conv.updated_at = updated_at
    db.commit()
    db.refresh(conv)
    return conv


def test_list_recent_conversations_returns_stock_threads(db):
    """按股票查询应返回最近活跃对话"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_conv(db, symbol="600519", market="SH", title="旧对话", updated_at=now - timedelta(hours=2))
    latest = _seed_conv(db, symbol="600519", market="SH", title="最新", updated_at=now)
    _seed_conv(db, symbol="000001", market="SZ", title="其他股票")

    rows = list_recent_conversations(symbol="600519", market="SH", limit=5, db=db)
    assert len(rows) == 2
    assert rows[0]["id"] == latest.id
    assert rows[0]["title"] == "最新"
    assert rows[0]["updated_at"]


def test_list_recent_conversations_empty_when_no_match(db):
    """无匹配股票时不应返回对话"""
    _seed_conv(db, symbol="600519", market="SH", title="茅台")
    rows = list_recent_conversations(symbol="999999", market="SH", limit=5, db=db)
    assert rows == []
