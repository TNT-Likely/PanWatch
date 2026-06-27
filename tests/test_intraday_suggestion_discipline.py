"""盘中监测建议纪律：今日流水与过激清仓降级。"""

from datetime import datetime, timezone

from src.agents.intraday_monitor import IntradayMonitorAgent
from src.core.position_trades_context import summarize_today_trades
from src.models.market import MarketCode, StockData
from src.web.models import Account, Position, PositionTrade, Stock


def _seed_bertili_trades(db):
    acc = Account(name="测试账户纪律", available_funds=100000, enabled=True)
    stock = Stock(symbol="603597", name="测试伯特利", market="CN")
    db.add(acc)
    db.add(stock)
    db.flush()
    pos = Position(
        account_id=acc.id,
        stock_id=stock.id,
        cost_price=29.63,
        quantity=8100,
        invested_amount=29.63 * 8100,
    )
    db.add(pos)
    db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(
        PositionTrade(
            position_id=pos.id,
            side="sell",
            price=26.44,
            quantity=400,
            amount=26.44 * 400,
            cost_before=29.63,
            qty_before=8500,
            cost_after=29.63,
            qty_after=8100,
            traded_at=now,
        )
    )
    db.add(
        PositionTrade(
            position_id=pos.id,
            side="buy",
            price=26.0,
            quantity=300,
            amount=26.0 * 300,
            cost_before=29.63,
            qty_before=8100,
            cost_after=29.63,
            qty_after=8400,
            traded_at=now,
        )
    )
    db.commit()
    return stock


def test_summarize_today_trades_net_position(db):
    """今日流水汇总应识别买卖对冲后的净变动"""
    stock = _seed_bertili_trades(db)
    summary = summarize_today_trades(db, symbol=stock.symbol, market=stock.market)
    assert summary["has_sell_today"] is True
    assert summary["has_buy_today"] is True
    assert summary["sell_qty"] == 400
    assert summary["buy_qty"] == 300
    assert summary["net_qty"] == -100
    assert "卖出" in summary["context"]


def test_adjust_suggestion_downgrades_repeat_liquidate_near_support():
    """今日已卖出且接近支撑时，清仓建议应降级为持有且不推送"""
    agent = IntradayMonitorAgent()
    stock = StockData(
        symbol="603597",
        name="测试伯特利",
        market=MarketCode.CN,
        current_price=26.43,
        change_pct=-4.38,
        change_amount=-1.2,
        volume=10000,
        turnover=200000,
        open_price=27.0,
        high_price=27.2,
        low_price=26.2,
        prev_close=27.6,
    )
    data = {
        "today_trades": {
            "has_sell_today": True,
            "has_buy_today": True,
            "sell_qty": 400,
            "buy_qty": 600,
            "net_qty": 200,
        },
        "kline_summary": {"support_m": 26.11, "support_s": 25.8},
    }
    raw = {
        "action": "sell",
        "action_label": "清仓",
        "signal": "止损预警",
        "reason": "浮亏超-10%",
        "should_alert": True,
    }
    adjusted = agent._adjust_suggestion_for_context(raw, data, stock)  # noqa: SLF001
    assert adjusted["action"] == "hold"
    assert adjusted["should_alert"] is False


def test_build_prompt_recent_trades_not_labeled_as_today():
    """近期交易记录不应误导模型把历史流水当成今日操作"""
    agent = IntradayMonitorAgent()
    stock = StockData(
        symbol="603596",
        name="伯特利",
        market=MarketCode.CN,
        current_price=24.93,
        change_pct=-3.11,
        change_amount=-0.8,
        volume=10000,
        turnover=200000,
        open_price=25.59,
        high_price=25.59,
        low_price=24.79,
        prev_close=25.73,
    )

    class _Pos:
        account_id = 1
        account_name = "华泰证券"
        cost_price = 29.22
        quantity = 2700
        trading_style = "long"

    class _Portfolio:
        total_available_funds = 0.0
        accounts = []

        def get_positions_for_stock(self, symbol):
            return [_Pos()]

    class _Ctx:
        portfolio = _Portfolio()

    data = {
        "stock_data": stock,
        "kline_summary": {},
        "symbol_context": {
            "constraints": {
                "recent_trades": [
                    {
                        "side": "buy",
                        "quantity": 300,
                        "price": 25.95,
                        "qty_after": 2700,
                        "cost_after": 29.222099,
                        "traded_at": "2026-06-24T03:08:55",
                        "is_today": False,
                    },
                    {
                        "side": "sell",
                        "quantity": 300,
                        "price": 26.47,
                        "qty_after": 2400,
                        "cost_after": 29.631111,
                        "traded_at": "2026-06-24T01:22:42",
                        "is_today": False,
                    },
                ]
            }
        },
        "today_trades": {
            "has_sell_today": False,
            "has_buy_today": False,
            "sell_qty": 0,
            "buy_qty": 0,
            "net_qty": 0,
            "context": "",
        },
    }
    _system, user_content = agent.build_prompt(data, _Ctx())
    assert "今日已买卖的，不要重复建议同方向操作" not in user_content
    assert "06-24" in user_content
    assert "\n## 今日持仓变动\n" not in user_content
    assert "今日已有卖出" not in user_content


def test_build_prompt_includes_today_trades_section():
    """盘中 prompt 应注入今日持仓变动"""
    agent = IntradayMonitorAgent()
    stock = StockData(
        symbol="603597",
        name="测试伯特利",
        market=MarketCode.CN,
        current_price=26.43,
        change_pct=-4.38,
        change_amount=-1.2,
        volume=10000,
        turnover=200000,
        open_price=27.0,
        high_price=27.2,
        low_price=26.2,
        prev_close=27.6,
    )

    class _Portfolio:
        total_available_funds = 100000.0
        accounts: list = []

        def get_positions_for_stock(self, symbol):
            return []

    class _Ctx:
        portfolio = _Portfolio()

    data = {
        "stock_data": stock,
        "kline_summary": {},
        "symbol_context": {},
        "today_trades": {
            "has_sell_today": True,
            "has_buy_today": False,
            "sell_qty": 400,
            "buy_qty": 0,
            "net_qty": -400,
            "context": "今日持仓变动：\n- 【今日】卖出 伯特利(CN:603596) 400股 @26.44",
        },
    }
    _system, user_content = agent.build_prompt(data, _Ctx())
    assert "今日持仓变动" in user_content
    assert "今日已有卖出" in user_content
