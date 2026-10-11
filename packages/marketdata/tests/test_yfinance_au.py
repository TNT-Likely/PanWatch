from marketdata.symbol import Market, Symbol
from marketdata.vendors.kline import YahooKlineVendor
from marketdata.vendors.yfinance import YFinanceQuoteVendor


class _FastInfo(dict):
    def __getitem__(self, k):
        return self.get(k)


def test_yfinance_quote_au_uses_ax_suffix(monkeypatch):
    captured = {}

    class _Ticker:
        def __init__(self, t):
            captured["ticker"] = t
            self.fast_info = _FastInfo(
                last_price=102.5, previous_close=100.0, open=100.4,
                day_high=103.0, day_low=100.1,
            )

    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", _Ticker)
    v = YFinanceQuoteVendor()
    quotes = v.fetch([Symbol(market=Market.AU, code="VAS")], config={})
    assert captured["ticker"] == "VAS.AX"
    assert quotes and quotes[0].symbol == "VAS"
    assert quotes[0].market == "AU"
    assert quotes[0].current_price == 102.5


def test_yahoo_kline_au_uses_ax_suffix(monkeypatch):
    from marketdata.vendors import kline as K

    payload = {
        "chart": {
            "result": [{
                "timestamp": [1760000000, 1760086400],
                "indicators": {
                    "quote": [{"open": [1.0, 1.1], "high": [1.2, 1.3], "low": [0.9, 1.0],
                               "close": [1.1, 1.2], "volume": [10, 11]}],
                    "adjclose": [{"adjclose": [1.1, 1.2]}],
                },
            }],
            "error": None,
        }
    }
    captured = {}

    def fake_market_get(url, **kw):
        captured["url"] = url
        return payload

    monkeypatch.setattr(K, "market_get", fake_market_get)
    bars = YahooKlineVendor().fetch([Symbol(market=Market.AU, code="CBA")], config={"days": 30})
    assert "CBA.AX" in captured["url"]
    assert len(bars) == 2
