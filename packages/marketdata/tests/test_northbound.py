"""北向资金(同花顺 hexin)vendor + client 方法测试。

离线 monkeypatch marketdata.vendors.northbound.market_get,不实抓(沙箱代理拦截 hexin)。
真实响应结构未经实抓校验,构造样例按背景描述(当日分钟序列,hgt/sgt 累计值)搭建。
"""
from __future__ import annotations

import marketdata.vendors.northbound as nb
from marketdata.client import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.ports import SourceConfig
from marketdata.types import NorthboundItem


def _hexin_payload(hgt: list, sgt: list, *, date: str | None = None) -> dict:
    inner = {"hgt": hgt, "sgt": sgt}
    if date is not None:
        inner["date"] = date
    return {"data": inner}


class TestUnwrapPayload:
    def test_single_layer(self):
        assert nb._unwrap_payload({"data": {"hgt": [], "sgt": []}}) == {"hgt": [], "sgt": []}

    def test_double_layer(self):
        assert nb._unwrap_payload({"data": {"data": {"hgt": [], "sgt": []}}}) == {"hgt": [], "sgt": []}

    def test_not_a_dict_returns_empty(self):
        assert nb._unwrap_payload(None) == {}
        assert nb._unwrap_payload("not a dict") == {}
        assert nb._unwrap_payload({"no_data_key": 1}) == {}


class TestLastPoint:
    def test_list_pairs(self):
        series = [["09:30", 1.1], ["09:31", 1.5], ["09:32", 2.3]]
        t, v = nb._last_point(series)
        assert t == "09:32" and v == 2.3

    def test_dict_points(self):
        series = [{"time": "09:30", "value": 1.1}, {"time": "09:31", "value": 1.5}]
        t, v = nb._last_point(series)
        assert t == "09:31" and v == 1.5

    def test_empty_or_invalid_returns_none_none(self):
        assert nb._last_point([]) == (None, None)
        assert nb._last_point(None) == (None, None)
        assert nb._last_point("not a series") == (None, None)


class TestToFloatAndSgtValid:
    def test_to_float_handles_nan_and_none(self):
        assert nb._to_float(None) is None
        assert nb._to_float(float("nan")) is None
        assert nb._to_float("1.23") == 1.23
        assert nb._to_float("not a number") is None

    def test_sgt_valid_rejects_nan(self):
        assert nb._sgt_valid(float("nan")) is None

    def test_sgt_valid_rejects_extreme_magnitude(self):
        assert nb._sgt_valid(999999.0) is None

    def test_sgt_valid_accepts_normal_range(self):
        assert nb._sgt_valid(12.34) == 12.34


# ---------------------------------------------------------------------------
# HexinNorthboundVendor.fetch
# ---------------------------------------------------------------------------

class TestHexinNorthboundVendor:
    def test_takes_last_value_of_minute_series(self, monkeypatch):
        payload = _hexin_payload(
            hgt=[["09:30", 1.2], ["09:31", 3.4], ["10:15", 8.76]],
            sgt=[["09:30", 0.5], ["09:31", 1.1], ["10:15", 2.34]],
            date="2026-07-16",
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {})
        assert len(out) == 1 and isinstance(out[0], NorthboundItem)
        item = out[0]
        assert item.date == "2026-07-16"
        assert item.hgt_net == 8.76
        assert item.sgt_net == 2.34
        assert item.total_net == 8.76 + 2.34
        assert item.time == "10:15"

    def test_sgt_nan_falls_back_to_none_and_total_none(self, monkeypatch):
        payload = _hexin_payload(
            hgt=[["09:30", 1.2], ["10:15", 8.76]],
            sgt=[["09:30", 0.5], ["10:15", float("nan")]],
            date="2026-07-16",
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {})
        assert len(out) == 1
        item = out[0]
        assert item.hgt_net == 8.76
        assert item.sgt_net is None
        assert item.total_net is None  # sgt 缺失,不臆造合计

    def test_sgt_extreme_magnitude_treated_as_invalid(self, monkeypatch):
        payload = _hexin_payload(
            hgt=[["10:15", 8.76]],
            sgt=[["10:15", 123456789.0]],  # 明显超出"亿元"合理范围的脏值
            date="2026-07-16",
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {})
        item = out[0]
        assert item.sgt_net is None
        assert item.total_net is None
        assert item.hgt_net == 8.76  # hgt 不受 sgt 异常污染

    def test_none_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: None)
        assert nb.HexinNorthboundVendor().fetch([], {}) == []

    def test_empty_dict_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: {})
        assert nb.HexinNorthboundVendor().fetch([], {}) == []

    def test_unexpected_structure_returns_empty(self, monkeypatch):
        # data 不是 dict,或没有 hgt/sgt 键 —— 防御性返回 []
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: {"data": "unexpected string"})
        assert nb.HexinNorthboundVendor().fetch([], {}) == []

        monkeypatch.setattr(nb, "market_get", lambda *a, **k: {"data": {"unrelated": 1}})
        assert nb.HexinNorthboundVendor().fetch([], {}) == []

    def test_both_series_empty_returns_empty(self, monkeypatch):
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: _hexin_payload(hgt=[], sgt=[]))
        assert nb.HexinNorthboundVendor().fetch([], {}) == []

    def test_date_falls_back_to_config_when_missing_in_response(self, monkeypatch):
        payload = _hexin_payload(hgt=[["10:15", 8.76]], sgt=[["10:15", 2.34]])  # 无 date 字段
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {"date": "2026-07-16"})
        assert out[0].date == "2026-07-16"

    def test_date_empty_string_when_unavailable_anywhere(self, monkeypatch):
        payload = _hexin_payload(hgt=[["10:15", 8.76]], sgt=[["10:15", 2.34]])
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {})
        assert out[0].date == ""

    def test_forwards_expected_request_shape(self, monkeypatch):
        captured = {}

        def fake_market_get(url, *, host_key=None, headers=None, **kwargs):
            captured["url"] = url
            captured["host_key"] = host_key
            captured["headers"] = headers
            return _hexin_payload(hgt=[["10:15", 1.0]], sgt=[["10:15", 1.0]])

        monkeypatch.setattr(nb, "market_get", fake_market_get)
        nb.HexinNorthboundVendor().fetch([], {})
        assert captured["url"] == "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
        assert captured["host_key"] == "data.hexin.cn"
        assert captured["headers"]["Host"] == "data.hexin.cn"
        assert captured["headers"]["Referer"] == "https://data.hexin.cn/"


# ---------------------------------------------------------------------------
# MarketData.northbound() —— 走单源 Engine 出数
# ---------------------------------------------------------------------------

class TestClientMethod:
    def test_northbound_via_single_source_engine(self, monkeypatch):
        payload = _hexin_payload(
            hgt=[["09:30", 1.2], ["10:15", 8.76]],
            sgt=[["09:30", 0.5], ["10:15", 2.34]],
            date="2026-07-16",
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        md = MarketData(config=StaticConfigProvider({
            "northbound": [SourceConfig(vendor="ths", priority=1)],
        }))
        out = md.northbound()
        assert len(out) == 1 and isinstance(out[0], NorthboundItem)
        assert out[0].hgt_net == 8.76
        assert out[0].total_net == 8.76 + 2.34

    def test_northbound_no_sources_returns_empty(self):
        md = MarketData(config=StaticConfigProvider({}))
        assert md.northbound() == []
