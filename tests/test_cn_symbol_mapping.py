import unittest

from marketdata.symbol import Symbol

from src.collectors.screenshot_collector import ScreenshotCollector
from src.core.cn_symbol import get_cn_exchange, get_cn_prefix, is_cn_sh
from src.models.market import MarketCode


class TestCnSymbolMapping(unittest.TestCase):
    def test_cn_exchange_core(self):
        """交易所识别 — SZ/SH/BJ 判断"""
        self.assertEqual(get_cn_exchange("000738"), "SZ")
        self.assertEqual(get_cn_exchange("600519"), "SH")
        self.assertEqual(get_cn_exchange("300750"), "SZ")
        self.assertEqual(get_cn_exchange("510300"), "SH")
        self.assertEqual(get_cn_exchange("900901"), "SH")
        self.assertEqual(get_cn_exchange("920001"), "BJ")

    def test_cn_prefix_core(self):
        """代码前缀 — 小写/大写前缀"""
        self.assertEqual(get_cn_prefix("000738"), "sz")
        self.assertEqual(get_cn_prefix("600519"), "sh")
        self.assertEqual(get_cn_prefix("920001"), "bj")
        self.assertEqual(get_cn_prefix("000738", upper=True), "SZ")
        self.assertTrue(is_cn_sh("600519"))
        self.assertFalse(is_cn_sh("000738"))

    def test_capital_flow_secid(self):
        """东方财富 secid — SZ 用 0 前缀，SH 用 1 前缀（marketdata 包 Symbol）"""
        self.assertEqual(Symbol.parse("000738", market="CN").to_eastmoney_secid(), "0.000738")
        self.assertEqual(Symbol.parse("600519", market="CN").to_eastmoney_secid(), "1.600519")

    # 雪球新闻 symbol 前缀映射测试已随 XueqiuNewsCollector 收口进 marketdata 包，
    # 对应用例见 packages/marketdata/tests/test_news.py::test_xueqiu_symbol_id_prefix_rules。

    def test_screenshot_urls(self):
        """截图 URL — 新浪/雪球/东方财富"""
        collector = ScreenshotCollector()
        self.assertEqual(
            collector._get_sina_url("000738", "CN"),
            "https://finance.sina.com.cn/realstock/company/sz000738/nc.shtml",
        )
        self.assertEqual(
            collector._get_xueqiu_url("000738", "CN"),
            "https://xueqiu.com/S/SZ000738",
        )
        self.assertEqual(
            collector._get_eastmoney_url("600519", "CN"),
            "https://quote.eastmoney.com/sh600519.html",
        )


if __name__ == "__main__":
    unittest.main()
