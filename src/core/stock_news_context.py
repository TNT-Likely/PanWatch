"""为 AI 对话/评估构建个股近期新闻与公告摘要。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.core.regulatory_red_flags import format_ai_context, scan_items

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_ANNOUNCEMENT_SOURCES = frozenset({"eastmoney"})


def _format_item_line(item, *, include_snippet: bool = False, snippet_len: int = 100) -> str:
    ts = item.publish_time.strftime("%m-%d %H:%M")
    line = f"- {item.title}（{ts}）"
    if include_snippet and item.content:
        snippet = " ".join(str(item.content).split())[:snippet_len]
        if snippet:
            line += f"：{snippet}"
    return line


async def fetch_stock_news_context(
    db: Session,
    symbol: str,
    *,
    since_hours: int = 72,
    news_limit: int = 5,
    announcement_limit: int = 5,
) -> str:
    """拉取近 N 小时新闻与公告标题摘要，失败降级为空串。"""
    try:
        from src.collectors.news_collector import NewsCollector, NewsItem
        from src.web.models import Stock

        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        name = stock.name if stock else symbol
        collector = NewsCollector.from_database()
        items: list[NewsItem] = await collector.fetch_all(
            symbols=[symbol], since_hours=since_hours, symbol_names={symbol: name}
        )
    except Exception as e:
        logger.debug(f"新闻/公告获取失败 {symbol}: {e}")
        return ""

    if not items:
        return ""

    announcements = [it for it in items if it.source in _ANNOUNCEMENT_SOURCES]
    news = [it for it in items if it.source not in _ANNOUNCEMENT_SOURCES]

    announcements = sorted(announcements, key=lambda x: x.publish_time, reverse=True)[:announcement_limit]
    news = sorted(news, key=lambda x: x.publish_time, reverse=True)[:news_limit]

    parts: list[str] = []
    reg = scan_items(items)
    reg_ctx = format_ai_context(reg)
    if reg_ctx:
        parts.append(reg_ctx)
    if announcements:
        lines = [_format_item_line(it, include_snippet=True) for it in announcements]
        parts.append("近期公告:\n" + "\n".join(lines))
    if news:
        lines = [_format_item_line(it, include_snippet=True) for it in news]
        parts.append("近期新闻:\n" + "\n".join(lines))

    return "\n\n".join(parts)
