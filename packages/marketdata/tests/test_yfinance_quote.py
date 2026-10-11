import marketdata.vendors.yfinance as yv
from marketdata.symbol import Symbol


def _payload(**meta):
    return {
        "chart": {
            "result": [{
                "meta": {
                    "regularMarketPrice": 150.0,
                    "previousClose": 148.0,
                    "regularMarketDayHigh": 151.0,
                    "regularMarketDayLow": 147.0,
                    "regularMarketVolume": 1000.0,
                    **meta,
                },
                "timestamp": [1760000000],
                "indicators": {"quote": [{"open": [149.0]}]},
            }],
            "error": None,
        }
    }


def test_yfinance_parses(monkeypatch):
    captured = {}

    def fake_market_get(url, **k):
        captured["url"] = url
        return _payload()

    monkeypatch.setattr(yv, "market_get", fake_market_get)
    out = yv.YFinanceQuoteVendor().fetch([Symbol.parse("AAPL")], {})
    assert len(out) == 1
    assert out[0].symbol == "AAPL" and out[0].market == "US"
    assert out[0].current_price == 150.0
    assert out[0].prev_close == 148.0 and out[0].open_price == 149.0
    assert out[0].high_price == 151.0 and out[0].low_price == 147.0
    assert out[0].change_amount == 2.0
    assert round(out[0].change_pct, 4) == round((150.0 - 148.0) / 148.0 * 100, 4)
    assert "AAPL" in captured["url"]


def test_yfinance_http_error_skips_symbol(monkeypatch):
    def fake_market_get(url, **k):
        raise RuntimeError("HTTP 429")

    monkeypatch.setattr(yv, "market_get", fake_market_get)
    out = yv.YFinanceQuoteVendor().fetch([Symbol.parse("AAPL")], {})
    assert out == []
