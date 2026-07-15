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


def test_yfinance_missing_lib_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", None)  # import 触发 ImportError
    with pytest.raises(VendorError):
        yv.YFinanceQuoteVendor().fetch([Symbol.parse("AAPL")], {})
