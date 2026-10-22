import sys
import types

import pytest

import marketdata.vendors.yfinance as yv
from marketdata.errors import VendorError
from marketdata.symbol import Symbol


def test_yfinance_parses(monkeypatch):
    fake = types.ModuleType("yfinance")

    class _T:
        def __init__(self, ticker):
            self.fast_info = {
                "last_price": 150.0, "previous_close": 148.0,
                "open": 149.0, "day_high": 151.0, "day_low": 147.0, "last_volume": 1000,
            }

    fake.Ticker = _T
    monkeypatch.setitem(sys.modules, "yfinance", fake)

    out = yv.YFinanceQuoteVendor().fetch([Symbol.parse("AAPL")], {})
    assert len(out) == 1
    assert out[0].symbol == "AAPL" and out[0].market == "US"
    assert out[0].current_price == 150.0
    assert round(out[0].change_pct, 4) == round((150.0 - 148.0) / 148.0 * 100, 4)


def test_yfinance_reads_fast_info_camel_case_mapping_keys(monkeypatch):
    fake = types.ModuleType("yfinance")

    class Ticker:
        def __init__(self, ticker):
            # fast_info.get() looks up the mapping keys, which are camelCase;
            # unlike fast_info["last_price"], it does not translate aliases.
            self.fast_info = {
                "lastPrice": 150.0,
                "previousClose": 148.0,
                "open": 149.0,
                "dayHigh": 151.0,
                "dayLow": 147.0,
                "lastVolume": 1000,
            }

    fake.Ticker = Ticker
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    errors = []
    monkeypatch.setattr(yv, "record_error", errors.append)

    out = yv.YFinanceQuoteVendor().fetch([Symbol.parse("AAPL")], {})

    assert len(out) == 1
    assert out[0].current_price == 150.0
    assert out[0].prev_close == 148.0
    assert out[0].high_price == 151.0
    assert out[0].low_price == 147.0
    assert out[0].volume == 1000
    assert errors == []


def test_yfinance_missing_lib_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", None)  # import 触发 ImportError
    with pytest.raises(VendorError):
        yv.YFinanceQuoteVendor().fetch([Symbol.parse("AAPL")], {})
