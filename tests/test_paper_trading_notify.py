"""模拟盘通知系统单元测试。"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.modules.paper_trading.paper_trading_engine import (
    _serialize_position,
    _serialize_trade,
)
from src.modules.paper_trading.paper_trading_notifier import (
    _format_entry_message,
    _format_exit_message,
    _format_premarket_plan,
    _format_daily_summary,
    _strategy_label,
)
from src.modules.administration.stock_link import stock_url





def _make_position(**kwargs):
    """创建模拟 PaperTradingPosition ORM 对象。"""
    defaults = {
        "id": 1,
        "stock_symbol": "002837",
        "stock_market": "CN",
        "stock_name": "英维克",
        "quantity": 100,
        "entry_price": 113.0,
        "stop_loss": 104.0,
        "target_price": 130.0,
        "current_price": 115.0,
        "unrealized_pnl": 200.0,
        "status": "open",
        "strategy_code": "trend_follow",
        "signal_run_id": 1,
        "signal_snapshot_date": "2026-04-16",
        "signal_action": "buy",
        "opened_at": None,
        "closed_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_trade(**kwargs):
    """创建模拟 PaperTradingTrade ORM 对象。"""
    defaults = {
        "id": 1,
        "stock_symbol": "002837",
        "stock_market": "CN",
        "stock_name": "英维克",
        "quantity": 100,
        "entry_price": 113.0,
        "exit_price": 120.0,
        "pnl": 700.0,
        "pnl_pct": 6.19,
        "exit_reason": "target_price",
        "holding_days": 3,
        "strategy_code": "trend_follow",
        "signal_run_id": 1,
        "signal_snapshot_date": "2026-04-16",
        "opened_at": None,
        "closed_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_account(**kwargs):
    defaults = {
        "current_capital": 900000.0,
        "initial_capital": 1000000.0,
        "enabled": True,
        "excluded_markets": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 盘前计划去重测试
# ---------------------------------------------------------------------------


class TestQlibPremarket(unittest.TestCase):
    def test_frozen_decision_displays_execution_date_and_lineage(self):
        decision = SimpleNamespace(payload={"execution_date": "2026-10-14", "run_id": "frozen-run",
            "model_id": "reviewed-model", "symbols": ["SH600000"]})
        title, body = _format_premarket_plan([decision], _make_account(), english=True)
        self.assertIn("Qlib", title)
        self.assertIn("2026-10-14", body)
        self.assertIn("frozen-run", body)
        self.assertIn("reviewed-model", body)
        self.assertNotIn("Score", body)


class TestMessageFormat(unittest.TestCase):
    def test_english_preference_localizes_paper_trading_notification_templates(self):
        position = {
            "stock_symbol": "002837", "stock_market": "CN", "stock_name": "Inovance",
            "quantity": 100, "entry_price": 113.0, "stop_loss": 104.0,
            "target_price": 130.0, "strategy_code": "trend_follow",
        }
        title, body = _format_entry_message(position, None, english=True)

        self.assertIn("Paper trading entry", title)
        self.assertIn("Entry:", body)
        self.assertIn("Trend continuation", body)

    def test_entry_message_format(self):
        """建仓通知 — 格式含价格/策略/链接"""
        pos_data = {
            "stock_symbol": "002837",
            "stock_market": "CN",
            "stock_name": "英维克",
            "quantity": 100,
            "entry_price": 113.0,
            "stop_loss": 104.0,
            "target_price": 130.0,
            "strategy_code": "trend_follow",
        }
        sig_data = {
            "strategy_code": "trend_follow",
            "rank_score": 100.0,
        }
        title, body = _format_entry_message(pos_data, sig_data)
        self.assertIn("建仓", title)
        self.assertIn("英维克", title)
        self.assertIn("113.00", body)
        self.assertIn("104.00", body)
        self.assertIn("130.00", body)
        self.assertNotIn("评分", body)
        self.assertIn("趋势延续", body)  # 中文策略名
        self.assertIn("xueqiu.com", body)  # 股票链接

    def test_entry_message_no_signal(self):
        """建仓通知 — 无信号时不报错"""
        pos_data = {
            "stock_symbol": "002837",
            "stock_market": "CN",
            "stock_name": "英维克",
            "quantity": 100,
            "entry_price": 113.0,
            "stop_loss": 104.0,
            "target_price": 130.0,
            "strategy_code": "trend_follow",
        }
        title, body = _format_entry_message(pos_data, None)
        self.assertIn("建仓", title)
        self.assertIn("趋势延续", body)

    def test_exit_message_format(self):
        """平仓通知 — 盈利格式含止盈/持仓天数"""
        pos_data = {
            "stock_symbol": "002837",
            "stock_market": "CN",
            "stock_name": "英维克",
        }
        trade_data = {
            "entry_price": 113.0,
            "exit_price": 120.0,
            "pnl": 700.0,
            "pnl_pct": 6.19,
            "exit_reason": "target_price",
            "holding_days": 3,
        }
        title, body = _format_exit_message(pos_data, trade_data)
        self.assertIn("平仓", title)
        self.assertIn("+700.00", title)
        self.assertIn("止盈", body)
        self.assertIn("113.00", body)
        self.assertIn("120.00", body)
        self.assertIn("3天", body)
        self.assertIn("xueqiu.com", body)  # 股票链接

    def test_exit_message_loss(self):
        """平仓通知 — 亏损时显示负号和止损"""
        pos_data = {"stock_symbol": "002837", "stock_market": "CN", "stock_name": "英维克"}
        trade_data = {
            "entry_price": 113.0,
            "exit_price": 105.0,
            "pnl": -800.0,
            "pnl_pct": -7.08,
            "exit_reason": "stop_loss",
            "holding_days": 1,
        }
        title, body = _format_exit_message(pos_data, trade_data)
        self.assertIn("-800.00", title)
        self.assertIn("止损", body)

    def test_daily_summary_format(self):
        """日终摘要 — 含总资产/平仓笔数/持仓数"""
        trades = [_make_trade()]
        positions = [_make_position()]
        account = _make_account()
        title, body = _format_daily_summary(trades, positions, account)
        self.assertIn("日终摘要", title)
        self.assertIn("总资产", body)
        self.assertIn("当日平仓 1 笔", body)
        self.assertIn("持仓中 1 只", body)


# ---------------------------------------------------------------------------
# 序列化函数测试
# ---------------------------------------------------------------------------


class TestSerialize(unittest.TestCase):
    def test_serialize_position(self):
        """序列化 — 持仓对象转 dict"""
        pos = _make_position()
        d = _serialize_position(pos)
        self.assertEqual(d["stock_symbol"], "002837")
        self.assertEqual(d["entry_price"], 113.0)
        self.assertEqual(d["strategy_code"], "trend_follow")
        self.assertIn("id", d)

    def test_serialize_trade(self):
        """序列化 — 交易记录转 dict"""
        trade = _make_trade()
        d = _serialize_trade(trade)
        self.assertEqual(d["exit_price"], 120.0)
        self.assertEqual(d["pnl"], 700.0)
        self.assertEqual(d["exit_reason"], "target_price")




class TestHelpers(unittest.TestCase):
    def test_strategy_label_known(self):
        """策略名映射 — 已知策略返回中文"""
        self.assertEqual(_strategy_label("trend_follow"), "趋势延续")
        self.assertEqual(_strategy_label("macd_golden"), "MACD金叉")
        self.assertEqual(_strategy_label("momentum"), "动量策略")
        self.assertEqual(_strategy_label("market_scan"), "市场扫描")

    def test_strategy_label_unknown(self):
        """策略名映射 — 未知策略原样返回"""
        self.assertEqual(_strategy_label("some_new_strategy"), "some_new_strategy")

    def test_stock_url_cn(self):
        """股票链接 — 深圳股票"""
        url = stock_url("002837", "CN", platform="xueqiu")
        self.assertIn("xueqiu.com/S/SZ002837", url)

    def test_stock_url_cn_sh(self):
        """股票链接 — 上海股票"""
        url = stock_url("600519", "CN", platform="xueqiu")
        self.assertIn("xueqiu.com/S/SH600519", url)

    def test_stock_url_us(self):
        """股票链接 — 美股"""
        url = stock_url("AAPL", "US", platform="xueqiu")
        self.assertEqual(url, "https://xueqiu.com/S/AAPL")

    def test_stock_url_hk(self):
        """股票链接 — 港股"""
        url = stock_url("00883", "HK", platform="xueqiu")
        self.assertEqual(url, "https://xueqiu.com/S/00883")


if __name__ == "__main__":
    unittest.main()
