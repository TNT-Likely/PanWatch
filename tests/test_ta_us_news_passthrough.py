"""美股 get_news 路由:应透传上游(Yahoo)而非被东财关键词搜索截走;中文行业词才走东财。

回归 bug:0.3.0 新闻分析师对美股 ticker(BABA)调 get_news,原逻辑只判 `not is_panwatch_routable`,
把美股 ticker 也送进东财关键词搜索 → 搜不到 → 返回「未搜到」空结果,美股拿不到个股新闻。
修复:关键词新闻分支再加「含中文」闸,纯字母 ticker 落到上游透传。
"""

from __future__ import annotations

from src.agents.tradingagents import toolkit_adapter as tk


def test_looks_like_cn_keyword_distinguishes_ticker_from_cn_query():
    """纯字母美股 ticker 不算中文行业词;含中文(行业/主题)才算。"""
    assert tk._looks_like_cn_keyword("汽车行业") is True
    assert tk._looks_like_cn_keyword("新能源汽车") is True
    assert tk._looks_like_cn_keyword("BABA") is False
    assert tk._looks_like_cn_keyword("NVDA") is False
    assert tk._looks_like_cn_keyword("") is False


def test_us_ticker_get_news_passes_through_to_upstream(monkeypatch):
    """美股 get_news(BABA)→ 走上游 vendor(Yahoo),不进东财关键词搜索。"""
    calls = {"keyword": 0, "upstream": 0}

    def fake_keyword(_sym):
        calls["keyword"] += 1
        return "东财关键词新闻"

    def fake_upstream(_method, *_a, **_k):
        calls["upstream"] += 1
        return "UPSTREAM YAHOO NEWS for BABA"

    monkeypatch.setattr(tk, "_serve_keyword_news", fake_keyword)
    monkeypatch.setattr(tk, "_real_route_to_vendor", fake_upstream)

    out = tk._patched_route_to_vendor("get_news", "BABA", "2026-06-15", "2026-06-22")

    assert calls["upstream"] == 1
    assert calls["keyword"] == 0
    assert "UPSTREAM" in str(out)


def test_cn_keyword_get_news_goes_to_eastmoney(monkeypatch):
    """中文行业词(汽车行业)→ 走东财关键词搜索,不透传上游。"""
    calls = {"keyword": 0, "upstream": 0}

    def fake_keyword(_sym):
        calls["keyword"] += 1
        return "东财搜到的行业新闻"

    def fake_upstream(_method, *_a, **_k):
        calls["upstream"] += 1
        return "UPSTREAM"

    monkeypatch.setattr(tk, "_serve_keyword_news", fake_keyword)
    monkeypatch.setattr(tk, "_real_route_to_vendor", fake_upstream)

    out = tk._patched_route_to_vendor("get_news", "汽车行业", "2026-06-15", "2026-06-22")

    assert calls["keyword"] == 1
    assert calls["upstream"] == 0
    assert "行业新闻" in str(out)
