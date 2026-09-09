"""回测内核(Phase 0)单元测试 —— 纯合成数据,不触发网络。"""

from src.modules.strategy.backtest import metrics as M
from src.modules.strategy.backtest.cost_model import CostModel
from src.modules.strategy.backtest.data_adapter import PriceBar
from src.modules.strategy.backtest.engine import Backtester, Signal, horizon_return


def _bar(date, o, h, low, c, v=1e6):
    return PriceBar(date=date, open=o, high=h, low=low, close=c, volume=v)


# ──────────────── 成本模型 ────────────────

def test_cost_model_stamp_duty_sell_only():
    """印花税仅卖出单边收取,买入不收。"""
    cm = CostModel()
    assert cm.fill("buy", 10.0, 1000).stamp_duty == 0.0
    assert cm.fill("sell", 10.0, 1000).stamp_duty > 0.0


def test_cost_model_min_commission():
    """小额成交佣金不低于最低 5 元。"""
    f = CostModel().fill("buy", 5.0, 100)  # gross≈500,万2.5≈0.125 → 取 5
    assert f.commission == 5.0


def test_round_trip_pnl_deducts_cost():
    """同价买卖应净亏损(被成本与滑点吃掉)。"""
    rt = CostModel().round_trip_pnl(10.0, 10.0, 1000)
    assert rt["pnl"] < 0
    assert rt["total_cost"] > 0


# ──────────────── 绩效指标 ────────────────

def test_metrics_max_drawdown():
    """最大回撤 = 峰值到谷底的最大跌幅。"""
    assert abs(M.max_drawdown([100, 120, 90, 110]) - (30 / 120)) < 1e-9


def test_metrics_win_rate():
    """胜率 = 正收益笔数 / 总笔数。"""
    assert M.win_rate([1, -1, 2, -3]) == 0.5


def test_metrics_profit_factor():
    """盈亏比 = 总盈利 / 总亏损绝对值。"""
    assert abs(M.profit_factor([3, -1, -1]) - 1.5) < 1e-9


# ──────────────── 回测引擎 ────────────────

def test_engine_entry_next_day():
    """信号次日开盘入场,防止用当日数据(无未来函数)。"""
    bars = [_bar("2026-01-01", 10, 10, 10, 10), _bar("2026-01-02", 11, 11, 11, 11),
            _bar("2026-01-06", 11, 12.5, 11, 12)]
    sig = Signal("X", "CN", "2026-01-01", stop_loss=9.0, target_price=12.0, holding_days=10)
    t = Backtester().run_single(sig, bars)
    assert t is not None and t.entry_date == "2026-01-02" and t.entry_price == 11


def test_engine_stop_loss():
    """价格跌破止损位按止损平仓。"""
    bars = [_bar("2026-01-01", 10, 10, 10, 10), _bar("2026-01-02", 10, 10.2, 9.9, 10),
            _bar("2026-01-05", 9.5, 9.6, 9.0, 9.2)]
    sig = Signal("X", "CN", "2026-01-01", stop_loss=9.5, target_price=12.0, holding_days=10)
    t = Backtester().run_single(sig, bars)
    assert t.exit_reason == "stop_loss"


def test_engine_target():
    """价格触及止盈位按止盈平仓。"""
    bars = [_bar("2026-01-01", 10, 10, 10, 10), _bar("2026-01-02", 10, 10, 10, 10),
            _bar("2026-01-05", 11, 12.5, 11, 12)]
    sig = Signal("X", "CN", "2026-01-01", stop_loss=9.0, target_price=12.0, holding_days=10)
    t = Backtester().run_single(sig, bars)
    assert t.exit_reason == "target"


def test_engine_expire():
    """达最大持有交易日按收盘平仓。"""
    bars = [_bar(f"2026-01-{d:02d}", 10, 10.1, 9.9, 10) for d in range(1, 15)]
    sig = Signal("X", "CN", "2026-01-01", stop_loss=5.0, target_price=20.0, holding_days=3)
    t = Backtester().run_single(sig, bars)
    assert t.exit_reason == "expire" and t.holding_bars == 3


def test_horizon_return_matches_manual():
    """horizon_return 复刻 StrategyOutcome 口径:(后收盘-基准)/基准。"""
    bars = [_bar("2026-01-01", 10, 10, 10, 10), _bar("2026-01-02", 10, 11, 10, 11),
            _bar("2026-01-06", 11, 12, 11, 12)]
    sig = Signal("X", "CN", "2026-01-01", entry_price=10.0)
    r = horizon_return(sig, bars, horizon_days=5)  # target_day=01-06 → outcome=12 → +20%
    assert r is not None and abs(r - 20.0) < 1e-6


def test_backtest_run_aggregates():
    """批量回测聚合净值曲线与指标。"""
    bars = [_bar(f"2026-01-{d:02d}", 10, 10.1, 9.9, 10) for d in range(1, 15)]
    sigs = [Signal("X", "CN", "2026-01-01", stop_loss=5, target_price=20, holding_days=3)]
    res = Backtester().run(sigs, {("X", "CN"): bars})
    assert len(res.trades) == 1 and res.metrics["trades"] == 1
    assert len(res.equity_curve) == 2
