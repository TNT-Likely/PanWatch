"""市场/资金面(龙虎榜/融资融券/股东户数/分红)vendor + client 方法测试。

离线 monkeypatch marketdata.vendors.market_flow.market_get,不实抓(沙箱拦东财 datacenter)。
"""

from __future__ import annotations

import marketdata.vendors.market_flow as mf
from marketdata.client import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.ports import SourceConfig
from marketdata.symbol import Symbol
from marketdata.types import DividendItem, DragonTigerItem, MarginItem, ShareholderItem


def _datacenter_payload(rows: list[dict]) -> dict:
    return {"result": {"data": rows}}


# ---------------------------------------------------------------------------
# _datacenter_get helper
# ---------------------------------------------------------------------------

class TestDatacenterGetHelper:
    def test_extracts_result_data(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload([{"A": 1}]))
        assert mf._datacenter_get("SOME_REPORT", "(X=1)", "SOME_COL") == [{"A": 1}]

    def test_none_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: None)
        assert mf._datacenter_get("SOME_REPORT", "(X=1)", "SOME_COL") == []

    def test_missing_result_key_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: {"no_result": True})
        assert mf._datacenter_get("SOME_REPORT", "(X=1)", "SOME_COL") == []

    def test_missing_data_key_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: {"result": {}})
        assert mf._datacenter_get("SOME_REPORT", "(X=1)", "SOME_COL") == []

    def test_forwards_expected_params(self, monkeypatch):
        captured = {}

        def fake_market_get(url, *, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return _datacenter_payload([])

        monkeypatch.setattr(mf, "market_get", fake_market_get)
        mf._datacenter_get("RPT_X", "(Y=1)", "SORT_COL", page_size=123)
        assert captured["url"] == "https://datacenter-web.eastmoney.com/api/data/v1/get"
        p = captured["params"]
        assert p["reportName"] == "RPT_X"
        assert p["filter"] == "(Y=1)"
        assert p["sortColumns"] == "SORT_COL"
        assert p["pageSize"] == 123
        assert p["sortTypes"] == -1
        assert p["columns"] == "ALL"


# ---------------------------------------------------------------------------
# 龙虎榜(市场级)
# ---------------------------------------------------------------------------

class TestDragonTiger:
    def _row(self):
        return {
            "TRADE_DATE": "2026-07-16 00:00:00",
            "SECURITY_CODE": "600519",
            "SECURITY_NAME_ABBR": "贵州茅台",
            "EXPLANATION": "日振幅值达15%的证券",
            "CLOSE_PRICE": 1700.5,
            "CHANGE_RATE": 3.2,
            "BILLBOARD_NET_AMT": 12345678.9,
            "BILLBOARD_BUY_AMT": 20000000.0,
            "BILLBOARD_SELL_AMT": 7654321.1,
            "TURNOVERRATE": 1.23,
        }

    def test_no_date_returns_empty_without_calling_market_get(self, monkeypatch):
        calls = {"n": 0}

        def fake_market_get(*a, **k):
            calls["n"] += 1
            return _datacenter_payload([])

        monkeypatch.setattr(mf, "market_get", fake_market_get)
        out = mf.EastmoneyDragonTigerVendor().fetch([], {})
        assert out == []
        assert calls["n"] == 0

    def test_parses_rows_with_date(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload([self._row()]))
        out = mf.EastmoneyDragonTigerVendor().fetch([], {"date": "2026-07-16"})
        assert len(out) == 1 and isinstance(out[0], DragonTigerItem)
        item = out[0]
        assert item.trade_date == "2026-07-16"
        assert item.symbol == "600519"
        assert item.name == "贵州茅台"
        assert item.reason == "日振幅值达15%的证券"
        assert item.close == 1700.5
        assert item.change_pct == 3.2
        assert item.net_buy == 12345678.9
        assert item.buy_amt == 20000000.0
        assert item.sell_amt == 7654321.1
        assert item.turnover_pct == 1.23

    def test_request_uses_date_filter(self, monkeypatch):
        captured = {}

        def fake_market_get(url, *, params=None, **kwargs):
            captured["params"] = params
            return _datacenter_payload([])

        monkeypatch.setattr(mf, "market_get", fake_market_get)
        mf.EastmoneyDragonTigerVendor().fetch([], {"date": "2026-07-16"})
        assert captured["params"]["filter"] == "(TRADE_DATE>='2026-07-16')(TRADE_DATE<='2026-07-16')"
        assert captured["params"]["reportName"] == "RPT_DAILYBILLBOARD_DETAILSNEW"
        assert captured["params"]["pageSize"] == 500

    def test_empty_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload([]))
        out = mf.EastmoneyDragonTigerVendor().fetch([], {"date": "2026-07-16"})
        assert out == []

    def test_broken_row_skipped_not_raised(self, monkeypatch):
        monkeypatch.setattr(
            mf, "market_get", lambda *a, **k: _datacenter_payload([None, self._row()])
        )
        out = mf.EastmoneyDragonTigerVendor().fetch([], {"date": "2026-07-16"})
        assert len(out) == 1


# ---------------------------------------------------------------------------
# 融资融券(按 symbol)
# ---------------------------------------------------------------------------

class TestMargin:
    def _row(self, date: str):
        return {
            "DATE": date,
            "RZYE": 5000000000.0,
            "RZMRE": 300000000.0,
            "RZCHE": 250000000.0,
            "RQYE": 80000000.0,
            "RQMCL": 1000000.0,
            "RQCHL": 900000.0,
            "RZRQYE": 5080000000.0,
        }

    def test_takes_latest_of_multiple_days(self, monkeypatch):
        # 构造某只多日(sortTypes=-1 已降序,data[0] 即最新)
        rows = [self._row("2026-07-16"), self._row("2026-07-15"), self._row("2026-07-14")]
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload(rows))
        out = mf.EastmoneyMarginVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert len(out) == 1 and isinstance(out[0], MarginItem)
        item = out[0]
        assert item.date == "2026-07-16"
        assert item.symbol == "600519"
        assert item.rz_balance == 5000000000.0
        assert item.rz_buy == 300000000.0
        assert item.rz_repay == 250000000.0
        assert item.rq_balance == 80000000.0
        assert item.rq_sell_vol == 1000000.0
        assert item.rq_repay_vol == 900000.0
        assert item.total_balance == 5080000000.0

    def test_filter_uses_scode_field(self, monkeypatch):
        captured = {}

        def fake_market_get(url, *, params=None, **kwargs):
            captured["filter"] = (params or {}).get("filter", "")
            return _datacenter_payload([self._row("2026-07-16")])

        monkeypatch.setattr(mf, "market_get", fake_market_get)
        mf.EastmoneyMarginVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert captured["filter"] == '(SCODE="600519")'

    def test_empty_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload([]))
        out = mf.EastmoneyMarginVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert out == []

    def test_no_symbols_returns_empty(self):
        assert mf.EastmoneyMarginVendor().fetch([], {}) == []

    def test_loops_multiple_symbols(self, monkeypatch):
        calls: list[str] = []

        def fake_market_get(url, *, params=None, **kwargs):
            filter_str = (params or {}).get("filter", "")
            calls.append(filter_str)
            return _datacenter_payload([self._row("2026-07-16")])

        monkeypatch.setattr(mf, "market_get", fake_market_get)
        out = mf.EastmoneyMarginVendor().fetch(
            [Symbol.parse("600519", market="CN"), Symbol.parse("000001", market="CN")], {}
        )
        assert len(out) == 2
        assert calls == ['(SCODE="600519")', '(SCODE="000001")']

    def test_exception_on_one_symbol_skipped_not_raised(self, monkeypatch):
        def fake_market_get(*a, **k):
            raise RuntimeError("网络异常")

        monkeypatch.setattr(mf, "market_get", fake_market_get)
        out = mf.EastmoneyMarginVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert out == []


# ---------------------------------------------------------------------------
# 股东户数(按 symbol)
# ---------------------------------------------------------------------------

class TestShareholders:
    def _row(self):
        return {
            "END_DATE": "2026-06-30 00:00:00",
            "HOLDER_NUM": 55000,
            "HOLDER_NUM_CHANGE": -1200,
            "HOLDER_NUM_RATIO": -2.13,
            "AVG_FREE_SHARES": 22800.5,
        }

    def test_parses_holder_fields(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload([self._row()]))
        out = mf.EastmoneyShareholdersVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert len(out) == 1 and isinstance(out[0], ShareholderItem)
        item = out[0]
        assert item.report_date == "2026-06-30"
        assert item.symbol == "600519"
        assert item.holder_num == 55000
        assert item.change_num == -1200
        assert item.change_ratio == -2.13
        assert item.avg_shares == 22800.5

    def test_filter_uses_security_code_field(self, monkeypatch):
        captured = {}

        def fake_market_get(url, *, params=None, **kwargs):
            captured["filter"] = (params or {}).get("filter", "")
            return _datacenter_payload([self._row()])

        monkeypatch.setattr(mf, "market_get", fake_market_get)
        mf.EastmoneyShareholdersVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert captured["filter"] == '(SECURITY_CODE="600519")'

    def test_empty_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload([]))
        out = mf.EastmoneyShareholdersVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert out == []

    def test_no_symbols_returns_empty(self):
        assert mf.EastmoneyShareholdersVendor().fetch([], {}) == []


# ---------------------------------------------------------------------------
# 分红(按 symbol,返回全部历史)
# ---------------------------------------------------------------------------

class TestDividend:
    def _rows(self):
        return [
            {
                "EX_DIVIDEND_DATE": "2026-06-20 00:00:00",
                "PRETAX_BONUS_RMB": 2.38,
                "TRANSFER_RATIO": 0.0,
                "BONUS_RATIO": 0.0,
                "ASSIGN_PROGRESS": "实施分配",
            },
            {
                "EX_DIVIDEND_DATE": "2025-06-21 00:00:00",
                "PRETAX_BONUS_RMB": 2.19,
                "TRANSFER_RATIO": 3.0,
                "BONUS_RATIO": 0.0,
                "ASSIGN_PROGRESS": "实施分配",
            },
        ]

    def test_parses_full_history(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload(self._rows()))
        out = mf.EastmoneyDividendVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert len(out) == 2
        assert all(isinstance(x, DividendItem) for x in out)
        first = out[0]
        assert first.ex_date == "2026-06-20"
        assert first.symbol == "600519"
        assert first.dividend_per_share == 2.38
        assert first.transfer_ratio == 0.0
        assert first.bonus_ratio == 0.0
        assert first.progress == "实施分配"
        second = out[1]
        assert second.ex_date == "2025-06-21"
        assert second.transfer_ratio == 3.0

    def test_filter_uses_security_code_field(self, monkeypatch):
        captured = {}

        def fake_market_get(url, *, params=None, **kwargs):
            captured["filter"] = (params or {}).get("filter", "")
            return _datacenter_payload(self._rows())

        monkeypatch.setattr(mf, "market_get", fake_market_get)
        mf.EastmoneyDividendVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert captured["filter"] == '(SECURITY_CODE="600519")'

    def test_empty_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload([]))
        out = mf.EastmoneyDividendVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert out == []

    def test_no_symbols_returns_empty(self):
        assert mf.EastmoneyDividendVendor().fetch([], {}) == []

    def test_broken_row_skipped_not_raised(self, monkeypatch):
        monkeypatch.setattr(
            mf, "market_get", lambda *a, **k: _datacenter_payload([None, self._rows()[0]])
        )
        out = mf.EastmoneyDividendVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert len(out) == 1


# ---------------------------------------------------------------------------
# MarketData 客户端方法(单源 Engine 出数)
# ---------------------------------------------------------------------------

class TestClientMethods:
    def test_dragon_tiger_via_single_source_engine(self, monkeypatch):
        row = {
            "TRADE_DATE": "2026-07-16 00:00:00",
            "SECURITY_CODE": "600519",
            "SECURITY_NAME_ABBR": "贵州茅台",
            "EXPLANATION": "日振幅值达15%的证券",
            "CLOSE_PRICE": 1700.5,
            "CHANGE_RATE": 3.2,
            "BILLBOARD_NET_AMT": 12345678.9,
            "BILLBOARD_BUY_AMT": 20000000.0,
            "BILLBOARD_SELL_AMT": 7654321.1,
            "TURNOVERRATE": 1.23,
        }
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload([row]))

        md = MarketData(config=StaticConfigProvider({
            "dragon_tiger": [SourceConfig(vendor="eastmoney", priority=1)],
        }))
        out = md.dragon_tiger(date="2026-07-16")
        assert len(out) == 1 and isinstance(out[0], DragonTigerItem)
        assert out[0].symbol == "600519"

    def test_dragon_tiger_no_date_returns_empty(self):
        md = MarketData(config=StaticConfigProvider({
            "dragon_tiger": [SourceConfig(vendor="eastmoney", priority=1)],
        }))
        assert md.dragon_tiger() == []

    def test_margin_via_single_source_engine(self, monkeypatch):
        row = {
            "DATE": "2026-07-16",
            "RZYE": 5000000000.0,
            "RZMRE": 300000000.0,
            "RZCHE": 250000000.0,
            "RQYE": 80000000.0,
            "RQMCL": 1000000.0,
            "RQCHL": 900000.0,
            "RZRQYE": 5080000000.0,
        }
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload([row]))

        md = MarketData(config=StaticConfigProvider({
            "margin": [SourceConfig(vendor="eastmoney", priority=1)],
        }))
        out = md.margin([Symbol.parse("600519", market="CN")])
        assert len(out) == 1 and isinstance(out[0], MarginItem)
        assert out[0].symbol == "600519" and out[0].rz_balance == 5000000000.0

    def test_shareholders_via_single_source_engine(self, monkeypatch):
        row = {
            "END_DATE": "2026-06-30",
            "HOLDER_NUM": 55000,
            "HOLDER_NUM_CHANGE": -1200,
            "HOLDER_NUM_RATIO": -2.13,
            "AVG_FREE_SHARES": 22800.5,
        }
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload([row]))

        md = MarketData(config=StaticConfigProvider({
            "shareholders": [SourceConfig(vendor="eastmoney", priority=1)],
        }))
        out = md.shareholders([Symbol.parse("600519", market="CN")])
        assert len(out) == 1 and isinstance(out[0], ShareholderItem)
        assert out[0].holder_num == 55000

    def test_dividend_via_single_source_engine(self, monkeypatch):
        rows = [
            {
                "EX_DIVIDEND_DATE": "2026-06-20",
                "PRETAX_BONUS_RMB": 2.38,
                "TRANSFER_RATIO": 0.0,
                "BONUS_RATIO": 0.0,
                "ASSIGN_PROGRESS": "实施分配",
            }
        ]
        monkeypatch.setattr(mf, "market_get", lambda *a, **k: _datacenter_payload(rows))

        md = MarketData(config=StaticConfigProvider({
            "dividend": [SourceConfig(vendor="eastmoney", priority=1)],
        }))
        out = md.dividend([Symbol.parse("600519", market="CN")])
        assert len(out) == 1 and isinstance(out[0], DividendItem)
        assert out[0].progress == "实施分配"

    def test_margin_no_sources_returns_empty(self):
        md = MarketData(config=StaticConfigProvider({}))
        out = md.margin(["600519"], market="CN")
        assert out == []
