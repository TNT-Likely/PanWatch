"""新浪日K vendor 单测(离线:monkeypatch market_get)。"""

from __future__ import annotations

import json

import marketdata.vendors.kline as kline_mod
from marketdata.symbol import Symbol
from marketdata.vendors.kline import SinaKlineVendor, fetch_sina_kline_raw

FIXTURE = [
    {"day": "2026-09-22", "open": "17.25", "high": "17.60", "low": "17.10",
     "close": "17.45", "volume": "123456"},
    {"day": "2026-09-23", "open": "17.50", "high": "17.90", "low": "17.40",
     "close": "17.80", "volume": "234567"},
]


def test_sina_kline_maps_bars(monkeypatch):
    captured = {}

    def fake_market_get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return json.loads(json.dumps(FIXTURE))

    monkeypatch.setattr(kline_mod, "market_get", fake_market_get)
    bars = fetch_sina_kline_raw("sh688570", 90)
    assert [b.date for b in bars] == ["2026-09-22", "2026-09-23"]
    assert bars[0].close == 17.45 and bars[1].volume == 234567.0
    assert captured["params"]["symbol"] == "sh688570"
    assert captured["params"]["scale"] == "240"


def test_sina_kline_vendor_cn_only(monkeypatch):
    monkeypatch.setattr(kline_mod, "market_get", lambda url, **k: FIXTURE)
    vendor = SinaKlineVendor()
    sym_cn = Symbol.parse("sh688570", market="CN")
    assert len(vendor.fetch([sym_cn], {"days": 90})) == 2
    # 非CN市场直接返回空(engine 亦会按 supports_markets 跳过)
    sym_us = Symbol.parse("AAPL", market="US")
    assert vendor.fetch([sym_us], {"days": 90}) == []


def test_sina_kline_fail_raises_for_engine(monkeypatch):
    """vendor 契约:取数失败抛异常,由 Engine 降级链统一兜底。"""
    def fake_market_get(url, **kwargs):
        raise RuntimeError("blocked")

    monkeypatch.setattr(kline_mod, "market_get", fake_market_get)
    import pytest

    with pytest.raises(RuntimeError):
        fetch_sina_kline_raw("sh688570", 90)
