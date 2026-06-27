"""模拟盘通知系统单元测试。"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.core.paper_trading_engine import (
    _serialize_position,
    _serialize_signal,
    _serialize_trade,
)
from src.core.paper_trading_notifier import (
    _dedup_signals,
    _format_entry_message,
    _format_exit_message,
    _format_premarket_plan,
    _format_daily_summary,
    _strategy_label,
)
from src.core.stock_link import stock_url


def _make_signal(**kwargs):
    """创建模拟 StrategySignalRun ORM 对象。"""
    defaults = {
        "id": 1,
        "stock_symbol": "002837",
        "stock_market": "CN",
        "stock_name": "英维克",
        "strategy_code": "trend_follow",
        "rank_score": 100.0,
        "entry_low": 112.01,
        "entry_high": 114.27,
        "action": "buy",
        "stop_loss": 108.0,
        "target_price": 125.0,
        "status": "active",
        "snapshot_date": "2026-04-16",
        "created_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_position(**kwargs):
    """创建模拟 PaperTradingPosition ORM 对象。"""
    defaults = {
        "id": 1,
        "stock_symbol": "002837",
        "stock_market": "CN",
        "stock_name": "英维克",
        "security_type": "stock",
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
        "security_type": "stock",
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


class TestPremarketDedup(unittest.TestCase):
    def test_dedup_same_stock_multiple_strategies(self):
        """盘前去重 — 同股票4策略合并为1条"""
        signals = [
            _make_signal(id=1, strategy_code="trend_follow", rank_score=100.0),
            _make_signal(id=2, strategy_code="macd_golden", rank_score=90.0),
            _make_signal(id=3, strategy_code="momentum", rank_score=80.0),
            _make_signal(id=4, strategy_code="market_scan", rank_score=70.0),
        ]
        deduped = _dedup_signals(signals)
        self.assertEqual(len(deduped), 1)
        sig, count = deduped[0]
        self.assertEqual(count, 4)
        self.assertEqual(sig.strategy_code, "trend_follow")  # highest rank_score
        self.assertEqual(sig.rank_score, 100.0)

    def test_dedup_different_stocks(self):
        """盘前去重 — 不同股票各自保留"""
        signals = [
            _make_signal(id=1, stock_symbol="002837", rank_score=100.0),
            _make_signal(id=2, stock_symbol="000001", stock_name="平安银行", rank_score=95.0),
            _make_signal(id=3, stock_symbol="002837", strategy_code="macd_golden", rank_score=80.0),
        ]
        deduped = _dedup_signals(signals)
        self.assertEqual(len(deduped), 2)
        # 002837 has 2 strategies
        self.assertEqual(deduped[0][1], 2)
        # 000001 has 1 strategy
        self.assertEqual(deduped[1][1], 1)

    def test_premarket_plan_format_with_dedup(self):
        """盘前计划 — 去重后格式化含策略数和链接"""
        signals = [
            _make_signal(id=1, strategy_code="trend_follow", rank_score=100.0),
            _make_signal(id=2, strategy_code="macd_golden", rank_score=90.0),
            _make_signal(id=3, strategy_code="momentum", rank_score=80.0),
            _make_signal(id=4, strategy_code="market_scan", rank_score=70.0),
        ]
        account = _make_account()
        title, body = _format_premarket_plan(signals, account)

        self.assertIn("盘前计划", title)
        # 股票只出现 1 次（symbol 出现在 "002837.CN" 和 URL 中）
        lines_with_stock = [l for l in body.split("\n") if "002837" in l]
        self.assertEqual(len(lines_with_stock), 1)
        # 显示中文策略名 + 数量
        self.assertIn("趋势延续 等4个策略", body)
        # 包含雪球链接
        self.assertIn("xueqiu.com", body)

    def test_premarket_plan_no_signals(self):
        """盘前计划 — 空信号显示无候选"""
        account = _make_account()
        title, body = _format_premarket_plan([], account)
        self.assertIn("无候选", body)


# ---------------------------------------------------------------------------
# 消息格式化测试（dict 输入）
# ---------------------------------------------------------------------------


class TestMessageFormat(unittest.TestCase):
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
        self.assertIn("100.0", body)  # rank_score
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

    def test_serialize_signal(self):
        """序列化 — 策略信号转 dict"""
        sig = _make_signal()
        d = _serialize_signal(sig)
        self.assertEqual(d["stock_symbol"], "002837")
        self.assertEqual(d["rank_score"], 100.0)
        self.assertEqual(d["strategy_code"], "trend_follow")


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
