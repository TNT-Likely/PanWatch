from marketdata.http import capture_errors
from marketdata.symbol import Market, Symbol
from marketdata.vendors import yfinance as YF
from marketdata.vendors.kline import YahooKlineVendor
from marketdata.vendors.yfinance import YFinanceQuoteVendor


def _chart_meta_payload(**meta_overrides):
    return {
        "chart": {
            "result": [{
                "meta": {
                    "regularMarketPrice": 102.5,
                    "chartPreviousClose": 100.0,
                    "regularMarketDayHigh": 103.0,
                    "regularMarketDayLow": 100.1,
                    "regularMarketVolume": 123400.0,
                    **meta_overrides,
                },
                "timestamp": [1760000000],
                "indicators": {"quote": [{"open": [100.4]}]},
            }],
            "error": None,
        }
    }


def test_yfinance_quote_au_direct_chart(monkeypatch):
    captured = {}

    def fake_market_get(url, *, host_key=None, params=None, headers=None, **k):
        captured.update(url=url, host_key=host_key, params=params, headers=headers)
        return _chart_meta_payload()

    monkeypatch.setattr(YF, "market_get", fake_market_get)
    quotes = YFinanceQuoteVendor().fetch([Symbol(market=Market.AU, code="VAS")], config={})
    assert "VAS.AX" in captured["url"]
    assert captured["host_key"] == "query2.finance.yahoo.com"
    assert captured["params"]["range"] == "1d"
    assert quotes and quotes[0].symbol == "VAS" and quotes[0].market == "AU"
    q = quotes[0]
    assert q.current_price == 102.5 and q.prev_close == 100.0
    assert q.open_price == 100.4 and q.high_price == 103.0 and q.low_price == 100.1
    assert q.change_amount == 2.5 and q.change_pct == 2.5
    assert q.volume == 123400.0


def test_yfinance_quote_missing_price_skips(monkeypatch):
    monkeypatch.setattr(YF, "market_get",
                        lambda *a, **k: _chart_meta_payload(regularMarketPrice=None))
    with capture_errors() as errs:
        quotes = YFinanceQuoteVendor().fetch([Symbol(market=Market.AU, code="VAS")], config={})
    assert quotes == []
    assert errs and "regularMarketPrice" in errs[0]


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
