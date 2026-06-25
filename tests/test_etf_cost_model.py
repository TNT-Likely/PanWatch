"""场内 ETF 成本模型测试 —— A 股 ETF 免印花税与过户费。"""

from src.core.backtest.cost_model import CostModel


def test_etf_buy_no_stamp_duty_no_transfer_fee():
    """ETF 买入不收印花税(本就单边)与过户费,但仍收佣金。"""
    f = CostModel().fill("buy", 10.0, 1000, security_type="etf")
    assert f.stamp_duty == 0.0
    assert f.transfer_fee == 0.0
    assert f.commission > 0.0


def test_etf_sell_no_stamp_duty_no_transfer_fee():
    """ETF 卖出免印花税与过户费(股票卖出需收印花税)。"""
    f = CostModel().fill("sell", 10.0, 1000, security_type="etf")
    assert f.stamp_duty == 0.0
    assert f.transfer_fee == 0.0


def test_stock_sell_keeps_stamp_duty_and_transfer_fee():
    """股票(security_type 默认 stock)卖出仍收印花税与过户费(回归保护)。"""
    f = CostModel().fill("sell", 10.0, 1000)
    assert f.stamp_duty > 0.0
    assert f.transfer_fee > 0.0


def test_etf_round_trip_cheaper_than_stock():
    """同等价位下,ETF 一买一卖总摩擦应低于股票。"""
    etf = CostModel().round_trip_pnl(10.0, 10.0, 1000, security_type="etf")
    stock = CostModel().round_trip_pnl(10.0, 10.0, 1000)
    assert etf["total_cost"] < stock["total_cost"]
