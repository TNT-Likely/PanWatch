from marketdata.symbol import Market, Symbol
from marketdata.vendors.news import YahooNewsVendor


def test_yahoo_news_maps_au(monkeypatch):
    captured = {}

    class _Ticker:
        def __init__(self, t):
            captured["ticker"] = t

        news = [{
            "id": 1,
            "content": {
                "title": "VAS hits record",
                "pubDate": "2026-10-10T23:00:00Z",
                "canonicalUrl": {"url": "https://finance.yahoo.com/news/vas-record"},
            },
        }]

    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", _Ticker)
    articles = YahooNewsVendor().fetch([Symbol(market=Market.AU, code="VAS")], config={})
    assert captured["ticker"] == "VAS.AX"
    assert len(articles) == 1
    a = articles[0]
    assert a.source == "yahoo" and a.title == "VAS hits record"
    assert a.symbols == ["VAS"]
    assert a.url.endswith("/vas-record")
