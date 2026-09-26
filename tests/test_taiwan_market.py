"""台股官方盤後資料的上市／上櫃轉換與搜尋。"""

from marketdata.symbol import Symbol
from marketdata.vendors import taiwan
from src.platform.marketdata import stock_list


class _Response:
    def __init__(self, rows):
        self.rows = rows
        self.encoding = None

    def raise_for_status(self):
        return None

    def json(self):
        return self.rows


class _Client:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url):
        if "twse" in url:
            return _Response([{
                "Date": "1150924", "Code": "2330", "Name": "台積電",
                "ClosingPrice": "1,000.00", "Change": "10.00",
                "OpeningPrice": "995", "HighestPrice": "1010", "LowestPrice": "990",
                "TradeVolume": "1,000,000", "TradeValue": "1,000,000,000",
            }])
        return _Response([{
            "Date": "1150924", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
            "Close": "500", "Change": "-5", "Open": "508", "High": "510", "Low": "495",
            "TradingShares": "200000", "TransactionAmount": "100000000",
        }])


def test_taiwan_snapshot_and_quote(monkeypatch):
    taiwan._CACHE = None
    monkeypatch.setattr(taiwan.httpx, "Client", _Client)
    rows = taiwan.taiwan_snapshot()
    assert {row["symbol"] for row in rows} == {"2330", "6488"}
    assert rows[0]["date"] == "2026-09-24"
    assert rows[0]["volume"] == 1_000_000
    quotes = taiwan.TaiwanQuoteVendor().fetch([Symbol.parse("2330", market="TW")], {})
    assert quotes[0].current_price == 1000
    assert quotes[0].prev_close == 990
    assert quotes[0].change_pct == 10 / 990 * 100


def test_taiwan_search_uses_exchange_snapshot(monkeypatch):
    monkeypatch.setattr(taiwan, "taiwan_snapshot", lambda: [
        {"symbol": "2330", "name": "台積電", "market": "TW"},
    ])
    assert stock_list.search_stocks("台積", "TW")[0]["symbol"] == "2330"
    assert stock_list.search_stocks("600519", "CN") == []
