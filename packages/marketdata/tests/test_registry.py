"""PACKAGE_VENDORS_BY_TYPE:包内各数据类型合法 vendor 权威,防与 Engine 实际注册漂移。"""

from __future__ import annotations

from marketdata import PACKAGE_VENDORS_BY_TYPE
from marketdata.defaults import StaticConfigProvider
from marketdata.client import MarketData
from marketdata.registry import VENDOR_CLASSES_BY_TYPE, build_vendors


def test_package_vendors_by_type_content():
    """内容必须与 client.py 现状(quote/kline/capital_flow/events 各 vendor)完全一致。"""
    assert PACKAGE_VENDORS_BY_TYPE == {
        "quote": frozenset({"tencent", "sina", "eastmoney", "yfinance"}),
        "kline": frozenset({"tencent", "stooq", "eastmoney", "yahoo"}),
        "capital_flow": frozenset({"eastmoney", "sina"}),
        "events": frozenset({"eastmoney"}),
        "flash_news": frozenset({"cls", "sina", "eastmoney"}),
        "news": frozenset({"xueqiu", "eastmoney_news", "eastmoney"}),
        "fundamentals": frozenset({"tencent", "eastmoney"}),
        "dragon_tiger": frozenset({"eastmoney"}),
        "margin": frozenset({"eastmoney"}),
        "shareholders": frozenset({"eastmoney"}),
        "dividend": frozenset({"eastmoney"}),
        "northbound": frozenset({"ths"}),
    }


def test_package_vendors_by_type_matches_actual_engine_registration():
    """与 MarketData 实例上各 Engine 实际注册的 vendors keys 一致,防漂移。"""
    md = MarketData(config=StaticConfigProvider({}))
    engines = {
        "quote": md._quote_engine,
        "kline": md._kline_engine,
        "capital_flow": md._capital_flow_engine,
        "events": md._events_engine,
        "flash_news": md._flash_news_engine,
        "fundamentals": md._fundamentals_engine,
        "dragon_tiger": md._dragon_tiger_engine,
        "margin": md._margin_engine,
        "shareholders": md._shareholders_engine,
        "dividend": md._dividend_engine,
        "northbound": md._northbound_engine,
    }
    for datatype, engine in engines.items():
        assert set(engine.vendors.keys()) == PACKAGE_VENDORS_BY_TYPE[datatype]


def test_build_vendors_instantiates_all_registered_classes():
    for datatype, classes in VENDOR_CLASSES_BY_TYPE.items():
        vendors = build_vendors(datatype)
        assert set(vendors.keys()) == set(classes.keys())
        for name, instance in vendors.items():
            assert instance.name == name


def test_build_vendors_unknown_datatype_returns_empty():
    assert build_vendors("not_a_real_type") == {}
