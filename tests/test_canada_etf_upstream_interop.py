"""Keep Canadian provider identity and upstream listed-ETF semantics together."""

import pytest

from marketdata.vendors import canada
from src.modules.automation.tradingagents.data_context import build_stock_metadata_context
from src.platform.marketdata import stock_list


@pytest.mark.parametrize("market_filter", ["TSX", ""])
@pytest.mark.parametrize("symbol,name,security_type,asset_type", [
    ("DLR-U", "Horizons US Dollar Currency", "ETF", "etf"),
    ("REI-UN", "RioCan Real Estate Investment Trust", "EQUITY", "stock"),
])
def test_canadian_search_preserves_authoritative_fund_type(
    monkeypatch, market_filter, symbol, name, security_type, asset_type,
):
    item = {"symbol": symbol, "name": name, "market": "TSX",
            "security_type": security_type, "source": "yahoo_canada"}
    monkeypatch.setattr(canada, "search_canadian", lambda *args: [item])
    monkeypatch.setattr(stock_list, "_realtime_search", lambda *args: [])
    monkeypatch.setattr(stock_list, "_cached_search", lambda *args, **kwargs: [])
    assert stock_list.search_stocks(symbol, market_filter) == [{**item, "asset_type": asset_type}]
    assert "asset_type" not in item


@pytest.mark.parametrize("asset_type,security_type", [("etf", None), (None, "ETF")])
def test_canadian_etf_metadata_combines_fund_and_currency_safety(asset_type, security_type):
    context = build_stock_metadata_context(
        "DLR-U", "Horizons US Dollar Currency", "TSX", currency="USD",
        asset_type=asset_type, security_type=security_type,
    )
    for expected in ("TSX:DLR-U", "currency: USD", ".TO/.V", "verified FX",
                     "not an operating company", "tracking error", "Do not invent holdings"):
        assert expected in context


def test_canadian_reit_metadata_does_not_reclassify_it_as_etf():
    context = build_stock_metadata_context(
        "REI-UN", "RioCan Real Estate Investment Trust", "TSX",
        currency="CAD", security_type="EQUITY", asset_type="stock",
    )
    assert "TSX:REI-UN" in context and "FFO/AFFO" in context
    assert "not an operating company" not in context
