"""fundamentals(基本面/财务)vendor + client 方法测试。离线 monkeypatch market_get,不实抓。"""

import marketdata.vendors.fundamentals as fv
from marketdata.client import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.ports import SourceConfig
from marketdata.symbol import Symbol
from marketdata.types import Fundamentals


def _tencent_line(code: str = "600519", name: str = "贵州茅台") -> str:
    """构造腾讯 qt.gtimg `~` 数组样例行,索引对齐 fv._parse_fundamentals_line:
    idx1=name idx2=code idx39=pe_ttm idx44=circulating_market_value idx45=total_market_value
    idx46=pb idx52=pe_static。其余位置填占位空串,保证下标存在。
    """
    parts = [""] * 53
    parts[1] = name
    parts[2] = code
    parts[39] = "28.5"     # pe_ttm
    parts[44] = "18000.3"  # circulating_market_value(亿)
    parts[45] = "21000.5"  # total_market_value(亿)
    parts[46] = "9.8"      # pb
    parts[52] = "30.1"     # pe_static
    return f'v_{code}="1~' + "~".join(parts[1:]) + '";'


class TestTencentFundamentals:
    def test_parses_valuation_fields(self, monkeypatch):
        line = _tencent_line()
        monkeypatch.setattr(fv, "_fetch_lines", lambda codes: [line])
        out = fv.TencentFundamentalsVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert len(out) == 1 and isinstance(out[0], Fundamentals)
        f = out[0]
        assert f.symbol == "600519" and f.name == "贵州茅台" and f.market == "CN"
        assert f.pe_ttm == 28.5
        assert f.pe_static == 30.1
        assert f.pb == 9.8
        assert f.total_market_value == 21000.5
        assert f.circulating_market_value == 18000.3
        # 财报类字段该源不提供,一律 None
        assert f.eps is None and f.roe is None and f.report_date == ""

    def test_empty_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fv, "_fetch_lines", lambda codes: [])
        out = fv.TencentFundamentalsVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert out == []

    def test_non_cn_symbols_skipped(self, monkeypatch):
        calls = {"n": 0}

        def fake_fetch_lines(codes):
            calls["n"] += 1
            return []

        monkeypatch.setattr(fv, "_fetch_lines", fake_fetch_lines)
        out = fv.TencentFundamentalsVendor().fetch([Symbol.parse("00700", market="HK")], {})
        assert out == []
        assert calls["n"] == 0


class TestEastmoneyFundamentalsCN:
    def _payload(self, code="600519", name="贵州茅台"):
        return {"data": {
            "f57": code, "f58": name,
            "f84": 1256197800,     # 总股本(股)
            "f85": 1256197800,     # 流通股本(股)
            "f116": 2100050000000,  # 总市值(raw 元) → /1e8 = 21000.5(亿)
            "f117": 2100050000000,  # 流通市值(raw 元) → /1e8 = 21000.5(亿)
        }}

    def test_parses_shares_and_market_value(self, monkeypatch):
        monkeypatch.setattr(fv, "market_get", lambda *a, **k: self._payload())
        out = fv.EastmoneyFundamentalsVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert len(out) == 1
        f = out[0]
        assert f.symbol == "600519" and f.name == "贵州茅台" and f.market == "CN"
        assert f.total_shares == 1256197800.0
        assert f.float_shares == 1256197800.0
        assert f.total_market_value == 21000.5
        assert f.circulating_market_value == 21000.5
        # push2 该端点未提供 PE/PB,一律 None
        assert f.pe_ttm is None and f.pb is None

    def test_empty_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fv, "market_get", lambda *a, **k: None)
        out = fv.EastmoneyFundamentalsVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert out == []


class TestEastmoneyFundamentalsUS:
    def _row(self):
        return {
            "BASIC_EPS": 6.13,
            "ROE_AVG": 147.25,
            "OPERATE_INCOME": 383285000000,
            "PARENT_HOLDER_NETPROFIT": 96995000000,
            "GROSS_PROFIT_RATIO": 46.21,
            "NET_PROFIT_RATIO": 25.31,
            "OPERATE_INCOME_YOY": 2.02,
            "REPORT_DATE": "2025-09-30 00:00:00",
        }

    def test_parses_gmainindicator_nasdaq_first_try(self, monkeypatch):
        calls: list[str] = []

        def fake_market_get(url, *, params=None, **kwargs):
            secucode = (params or {}).get("filter", "")
            calls.append(secucode)
            if 'SECUCODE="AAPL.O"' in secucode:
                return {"result": {"data": [self._row()]}}
            return {"result": {"data": []}}

        monkeypatch.setattr(fv, "market_get", fake_market_get)
        out = fv.EastmoneyFundamentalsVendor().fetch([Symbol.parse("AAPL", market="US")], {})
        assert len(out) == 1
        f = out[0]
        assert f.symbol == "AAPL" and f.market == "US"
        assert f.eps == 6.13
        assert f.roe == 147.25
        assert f.revenue == 383285000000
        assert f.net_profit == 96995000000
        assert f.gross_margin == 46.21
        assert f.net_margin == 25.31
        assert f.revenue_yoy == 2.02
        assert f.report_date == "2025-09-30"
        # 首试 .O 即命中,不应再尝试 .N
        assert len(calls) == 1

    def test_falls_back_to_nyse_when_nasdaq_empty(self, monkeypatch):
        calls: list[str] = []

        def fake_market_get(url, *, params=None, **kwargs):
            secucode = (params or {}).get("filter", "")
            calls.append(secucode)
            if 'SECUCODE="GE.N"' in secucode:
                return {"result": {"data": [self._row()]}}
            return {"result": {"data": []}}

        monkeypatch.setattr(fv, "market_get", fake_market_get)
        out = fv.EastmoneyFundamentalsVendor().fetch([Symbol.parse("GE", market="US")], {})
        assert len(out) == 1
        assert out[0].symbol == "GE"
        # 先试 .O(空)再试 .N(命中)
        assert len(calls) == 2

    def test_both_empty_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fv, "market_get", lambda *a, **k: {"result": {"data": []}})
        out = fv.EastmoneyFundamentalsVendor().fetch([Symbol.parse("XXXX", market="US")], {})
        assert out == []


class TestEastmoneyFundamentalsHK:
    def _row(self):
        return {
            "BASIC_EPS": 4.55,
            "ROE_AVG": 22.1,
            "OPERATE_INCOME": 609015000000,
            "PARENT_HOLDER_NETPROFIT": 157688000000,
            "GROSS_PROFIT_RATIO": 34.2,
            "NET_PROFIT_RATIO": 25.9,
            "OPERATE_INCOME_YOY": 8.0,
            "REPORT_DATE": "2025-12-31 00:00:00",
            "BPS": 23.4,
            "DIVI_RATIO": 1.8,
        }

    def test_parses_gmainindicator_hk_with_bps_and_dividend(self, monkeypatch):
        calls: list[str] = []

        def fake_market_get(url, *, params=None, **kwargs):
            calls.append((params or {}).get("filter", ""))
            return {"result": {"data": [self._row()]}}

        monkeypatch.setattr(fv, "market_get", fake_market_get)
        out = fv.EastmoneyFundamentalsVendor().fetch([Symbol.parse("00700", market="HK")], {})
        assert len(out) == 1
        f = out[0]
        assert f.symbol == "00700" and f.market == "HK"
        assert f.bps == 23.4
        assert f.dividend_yield == 1.8
        assert f.report_date == "2025-12-31"
        assert calls == ['(SECUCODE="00700.HK")']

    def test_empty_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fv, "market_get", lambda *a, **k: {"result": {"data": []}})
        out = fv.EastmoneyFundamentalsVendor().fetch([Symbol.parse("00700", market="HK")], {})
        assert out == []


class TestEastmoneyFundamentalsMisc:
    def test_no_symbols_returns_empty(self):
        assert fv.EastmoneyFundamentalsVendor().fetch([], {}) == []

    def test_exception_on_one_symbol_skipped_not_raised(self, monkeypatch):
        def fake_market_get(*a, **k):
            raise RuntimeError("网络异常")

        monkeypatch.setattr(fv, "market_get", fake_market_get)
        out = fv.EastmoneyFundamentalsVendor().fetch([Symbol.parse("600519", market="CN")], {})
        assert out == []


def test_client_fundamentals_via_single_source_engine(monkeypatch):
    """MarketData.fundamentals() 走单源(tencent)Engine,能正确分组、汇总、出数。"""
    line = _tencent_line("600519", "贵州茅台")
    monkeypatch.setattr(fv, "_fetch_lines", lambda codes: [line])

    md = MarketData(config=StaticConfigProvider({
        "fundamentals": [SourceConfig(vendor="tencent", priority=1)],
    }))
    out = md.fundamentals([Symbol.parse("600519", market="CN")])
    assert len(out) == 1 and isinstance(out[0], Fundamentals)
    assert out[0].symbol == "600519" and out[0].pe_ttm == 28.5


def test_client_fundamentals_no_sources_returns_empty():
    md = MarketData(config=StaticConfigProvider({}))
    out = md.fundamentals(["600519"], market="CN")
    assert out == []
