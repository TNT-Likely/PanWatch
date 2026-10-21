"""Regression coverage for report symbols rejected by Telegram's Markdown parser."""
import asyncio
from html import escape, unescape
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.platform.notifications.notifier import NotifierManager


@pytest.mark.parametrize("title", ["盘中报告 <A&B>", ""])
def test_telegram_escapes_report_text_without_markdown_parsing(monkeypatch, title):
    client = AsyncMock()
    client.post.return_value = MagicMock(json=lambda: {"ok": True})
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr("src.platform.notifications.notifier.httpx.AsyncClient", lambda **kwargs: context)
    monkeypatch.setattr("src.platform.notifications.notifier.get_global_proxy", lambda: "")
    content = "historical_context [未闭合 *符号\n价格 < 10 & 风险 > 2"

    asyncio.run(NotifierManager()._send_telegram(
        {"bot_token": "test-token", "chat_id": "123"}, title, content))

    payload = client.post.call_args.kwargs["json"]
    assert payload["parse_mode"] == "HTML"
    expected = f"<b>{escape(title)}</b>\n\n{escape(content)}" if title else escape(content)
    assert payload["text"] == expected


def test_telegram_truncates_before_html_escaping_and_preserves_report_link(monkeypatch):
    client = AsyncMock()
    client.post.return_value = MagicMock(json=lambda: {"ok": True})
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr("src.platform.notifications.notifier.httpx.AsyncClient", lambda **kwargs: context)
    monkeypatch.setattr("src.platform.notifications.notifier.get_global_proxy", lambda: "")
    link = "https://example.com/report?stock=600519&session=intraday"
    asyncio.run(NotifierManager()._send_telegram(
        {"bot_token": "test-token", "chat_id": "123"}, "盘中报告", "<&" * 3000 + "\n\n" + link))

    payload = client.post.call_args.kwargs["json"]
    assert payload["parse_mode"] == "HTML"
    assert payload["text"].startswith("<b>盘中报告</b>\n\n")
    body = payload["text"].removeprefix("<b>盘中报告</b>\n\n")
    plain = unescape(body)
    assert body == escape(plain)  # Every escaped entity remains intact.
    assert len("盘中报告\n\n" + plain) <= 3900
    assert "内容过长已截断" in plain and plain.endswith(link)
