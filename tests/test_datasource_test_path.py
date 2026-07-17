"""数据源测试后端(quote/kline)切到 marketdata 包单源 Engine 的行为验证。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from marketdata import Bar, Quote
from src.core.data_collector import DataCollectorManager


def _make_source(**kwargs):
    defaults = dict(
        name="测试源",
        type="quote",
        provider="tencent",
        config={},
        test_symbols=["600519"],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestQuoteSourceTestPath(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_items_and_count(self):
        """quote 测试:monkeypatch MarketData.quotes 返回固定数据,断言 count>0/items/无 error"""
        fixed_quotes = [
            Quote(
                symbol="600519",
                market="CN",
                current_price=1700.0,
                name="贵州茅台",
                change_pct=1.2,
            ),
        ]

        with mock.patch(
            "marketdata.MarketData.quotes",
            lambda self, symbols, *, market=None: fixed_quotes,
        ):
            manager = DataCollectorManager()
            source = _make_source(type="quote", provider="tencent", test_symbols=["600519"])
            result = await manager._test_quote_source(source, source.test_symbols)

        self.assertTrue(result.success)
        self.assertEqual(result.error, "")
        self.assertEqual(result.count, 1)
        self.assertEqual(
            result.data,
            [{"symbol": "600519", "name": "贵州茅台", "price": 1700.0, "change_pct": 1.2}],
        )

    async def test_unbacked_provider_returns_clean_error_not_raise(self):
        """quote 测试:provider 在包内无对应 vendor 应返回明确 error,不抛异常"""
        manager = DataCollectorManager()
        source = _make_source(
            type="quote", provider="not_a_real_vendor", test_symbols=["600519"]
        )
        result = await manager._test_quote_source(source, source.test_symbols)

        self.assertFalse(result.success)
        self.assertIn("not_a_real_vendor", result.error)
        self.assertEqual(result.count, 0)


class TestKlineSourceTestPath(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_items_and_count(self):
        """kline 测试:monkeypatch MarketData.klines 返回固定数据,断言 count>0/items/无 error"""
        fixed_bars = [
            Bar(date="2026-07-15", open=1.0, close=1.1, high=1.2, low=0.9, volume=100.0),
            Bar(date="2026-07-16", open=1.1, close=1.2, high=1.3, low=1.0, volume=110.0),
        ]

        with mock.patch(
            "marketdata.MarketData.klines",
            lambda self, symbol, *, market, days=120, min_count=1: fixed_bars,
        ):
            manager = DataCollectorManager()
            source = _make_source(type="kline", provider="tencent", test_symbols=["600519"])
            result = await manager._test_kline_source(source, source.test_symbols)

        self.assertTrue(result.success)
        self.assertEqual(result.error, "")
        self.assertEqual(result.count, 1)
        self.assertEqual(
            result.data,
            [{"symbol": "600519", "last_close": 1.2, "last_date": "2026-07-16", "count": 2}],
        )

    async def test_unbacked_provider_returns_clean_error_not_raise(self):
        """kline 测试:provider 在包内无对应 vendor(如 tushare)应返回明确 error,不抛异常"""
        manager = DataCollectorManager()
        source = _make_source(type="kline", provider="tushare", test_symbols=["600519"])
        result = await manager._test_kline_source(source, source.test_symbols)

        self.assertFalse(result.success)
        self.assertIn("tushare", result.error)
        self.assertEqual(result.count, 0)


if __name__ == "__main__":
    unittest.main()
