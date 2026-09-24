"""全球指数/宏观指标数据源的宿主侧接线验证。

覆盖评审指出的三处接线:
1. datasources.TYPE_LABELS / _ENGINE_ATTACHED_TYPES 含新类型(页面标签与 engine_attached 标记);
2. data_collector._test_source_impl 能路由到新类型的测试分支(不再返回"不支持的数据源类型");
3. 两个 _test_* 分支的单源 Engine 行为:成功/全源失败(ok=False 透真因)/provider 无 vendor。

全部离线:monkeypatch/mock marketdata.MarketData 的取数方法,不发外部请求。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from marketdata import GlobalIndexQuote, MacroIndicator, Response

import src.modules.administration.api.datasources as ds
from src.modules.market.data_collector import CollectorResult, DataCollectorManager


def _make_source(**kwargs):
    defaults = dict(
        name="测试源",
        type="global_markets",
        provider="tencent_global",
        config={},
        test_symbols=[],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestTypeWiring(unittest.TestCase):
    """接线三处:TYPE_LABELS / _ENGINE_ATTACHED_TYPES / _test_source_impl 路由。"""

    def test_type_labels_contain_new_types(self):
        self.assertEqual(ds.TYPE_LABELS.get("global_markets"), "全球指数")
        self.assertEqual(ds.TYPE_LABELS.get("macro"), "宏观指标")

    def test_engine_attached_types_contain_new_types(self):
        self.assertIn("global_markets", ds._ENGINE_ATTACHED_TYPES)
        self.assertIn("macro", ds._ENGINE_ATTACHED_TYPES)

    def test_to_response_marks_new_types_engine_attached(self):
        row = SimpleNamespace(id=1, name="腾讯全球指数", type="global_markets",
                              provider="tencent_global", config={}, enabled=True,
                              priority=10, supports_batch=False, test_symbols=[])
        out = ds._to_response(row)
        self.assertTrue(out["engine_attached"])
        self.assertEqual(out["type_label"], "全球指数")

        row2 = SimpleNamespace(id=2, name="akshare宏观指标", type="macro",
                               provider="akshare", config={}, enabled=True,
                               priority=10, supports_batch=False, test_symbols=[])
        out2 = ds._to_response(row2)
        self.assertTrue(out2["engine_attached"])
        self.assertEqual(out2["type_label"], "宏观指标")

    def test_source_impl_routes_global_markets(self):
        """_test_source_impl 应路由 global_markets 到专用分支,而非落到"不支持"兜底"""
        import asyncio

        manager = DataCollectorManager()
        marker = CollectorResult(success=True, count=1)
        with mock.patch.object(manager, "_test_global_markets_source",
                               new=mock.AsyncMock(return_value=marker)) as m:
            result = asyncio.run(manager._test_source_impl(_make_source(type="global_markets"), []))
        self.assertIs(result, marker)
        self.assertEqual(m.await_count, 1)

    def test_source_impl_routes_macro(self):
        import asyncio

        manager = DataCollectorManager()
        marker = CollectorResult(success=True, count=1)
        with mock.patch.object(manager, "_test_macro_source",
                               new=mock.AsyncMock(return_value=marker)) as m:
            result = asyncio.run(manager._test_source_impl(_make_source(type="macro"), []))
        self.assertIs(result, marker)
        self.assertEqual(m.await_count, 1)


class TestGlobalMarketsTestPath(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_items_and_count(self):
        fixed = Response(ok=True, data=[
            GlobalIndexQuote(symbol="DJI", name="道琼斯", price=44000.5,
                             change_pct=-0.52, close=44230.0, source_tag="tencent_global"),
        ])
        with mock.patch("marketdata.MarketData.global_markets",
                        lambda self, **kw: fixed):
            manager = DataCollectorManager()
            result = await manager._test_global_markets_source(_make_source())

        self.assertTrue(result.success)
        self.assertEqual(result.error, "")
        self.assertEqual(result.count, 1)
        self.assertEqual(
            result.data,
            [{"symbol": "DJI", "name": "道琼斯", "price": 44000.5, "change_pct": -0.52}],
        )

    async def test_all_fail_response_ok_false_surfaces_error(self):
        """全源失败:包内返回 Response(ok=False) 不抛异常,测试分支透出 resp.error 真因"""
        failed = Response(ok=False, data=None, error="no enabled provider")
        with mock.patch("marketdata.MarketData.global_markets",
                        lambda self, **kw: failed):
            manager = DataCollectorManager()
            result = await manager._test_global_markets_source(_make_source())

        self.assertFalse(result.success)
        self.assertEqual(result.error, "no enabled provider")
        self.assertEqual(result.count, 0)

    async def test_unbacked_provider_returns_clean_error_not_raise(self):
        manager = DataCollectorManager()
        source = _make_source(provider="not_a_real_vendor")
        result = await manager._test_global_markets_source(source)
        self.assertFalse(result.success)
        self.assertIn("无对应 vendor", result.error)


class TestMacroTestPath(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_items_and_count(self):
        fixed = Response(ok=True, data=[
            MacroIndicator(name="制造业PMI", value=49.8, period="2026年08月",
                           unit="%", source="macro_china_pmi"),
        ])
        with mock.patch("marketdata.MarketData.macro",
                        lambda self, **kw: fixed):
            manager = DataCollectorManager()
            result = await manager._test_macro_source(_make_source(type="macro", provider="akshare"))

        self.assertTrue(result.success)
        self.assertEqual(result.error, "")
        self.assertEqual(result.count, 1)
        self.assertEqual(
            result.data,
            [{"name": "制造业PMI", "value": 49.8, "period": "2026年08月", "unit": "%"}],
        )

    async def test_all_fail_response_ok_false_surfaces_error(self):
        failed = Response(ok=False, data=None, error="no enabled provider")
        with mock.patch("marketdata.MarketData.macro",
                        lambda self, **kw: failed):
            manager = DataCollectorManager()
            result = await manager._test_macro_source(_make_source(type="macro", provider="akshare"))

        self.assertFalse(result.success)
        self.assertEqual(result.error, "no enabled provider")

    async def test_unbacked_provider_returns_clean_error_not_raise(self):
        manager = DataCollectorManager()
        source = _make_source(type="macro", provider="not_a_real_vendor")
        result = await manager._test_macro_source(source)
        self.assertFalse(result.success)
        self.assertIn("无对应 vendor", result.error)


if __name__ == "__main__":
    unittest.main()
