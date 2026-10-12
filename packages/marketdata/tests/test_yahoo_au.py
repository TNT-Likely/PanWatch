"""yahoo_au 行情 vendor:chart v8 解析、币种校验、失败容错;HK/US 路径不受影响。"""
from datetime import date, datetime, timezone

from marketdata.vendors.yahoo_au import YahooAUQuoteVendor


def _payload(price=100.0, prev=99.0, currency="AUD", ts=1_790_000_000):
    return {"chart": {"result": [{
        "meta": {
            "currency": currency, "regularMarketPrice": price,
            "chartPreviousClose": prev, "regularMarketTime": ts,
            "shortName": "Vanguard Australian Shares",
            "exchangeName": "ASX",
        },
    }], "error": None}}


def _fetch(monkeypatch, payload):
    calls = {}
    def fake_get(url, **kw):
        calls["url"] = url
        return payload
    monkeypatch.setattr("marketdata.vendors.yahoo_au.market_get", fake_get)
    return calls


def test_parses_price_change_and_metadata(monkeypatch):
    from marketdata.symbol import Market, Symbol
    _fetch(monkeypatch, _payload())
    out = YahooAUQuoteVendor().fetch([Symbol(Market.AU, "VAS")], {})
    assert len(out) == 1
    q = out[0]
    assert q.current_price == 100.0
    assert q.prev_close == 99.0
    assert abs(q.change_pct - (100.0 - 99.0) / 99.0 * 100) < 1e-9
    assert q.source == "yahoo_au"
    assert q.currency == "AUD"
    assert q.quote_date is not None and isinstance(q.quote_date, date)
    assert isinstance(q.source_timestamp, datetime) and q.source_timestamp.tzinfo is not None


def test_currency_mismatch_rejected(monkeypatch):
    from marketdata.symbol import Market, Symbol
    _fetch(monkeypatch, _payload(currency="USD"))
    out = YahooAUQuoteVendor().fetch([Symbol(Market.AU, "VAS")], {})
    assert out == []


def test_http_failure_returns_empty(monkeypatch):
    from marketdata.symbol import Market, Symbol
    def boom(url, **kw):
        raise RuntimeError("http down")
    monkeypatch.setattr("marketdata.vendors.yahoo_au.market_get", boom)
    out = YahooAUQuoteVendor().fetch([Symbol(Market.AU, "VAS")], {})
    assert out == []


def test_registry_has_yahoo_au_and_kline_au():
    from marketdata.registry import VENDOR_CLASSES_BY_TYPE
    from marketdata.vendors.kline import YahooKlineVendor
    assert VENDOR_CLASSES_BY_TYPE["quote"]["yahoo_au"] is YahooAUQuoteVendor
    assert "AU" in YahooKlineVendor.supports_markets


def test_hk_us_quote_vendor_untouched():
    """既有 yfinance vendor 不支持 AU(共享路径保持不变)。"""
    from marketdata.vendors.yfinance import YFinanceQuoteVendor
    assert "AU" not in YFinanceQuoteVendor.supports_markets
