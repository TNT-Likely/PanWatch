"""组合诊断(Phase 4)单元测试 —— 纯函数,不触发 DB。"""

from src.modules.portfolio.portfolio_diagnostics import diagnose_positions, herfindahl


def test_herfindahl_fully_concentrated():
    """全压一只 → HHI = 1。"""
    assert abs(herfindahl([100, 0, 0, 0]) - 1.0) < 1e-9


def test_herfindahl_evenly_diversified():
    """四只等权 → HHI = 0.25。"""
    assert abs(herfindahl([25, 25, 25, 25]) - 0.25) < 1e-9


def test_diagnose_empty():
    """空持仓不报错,计数为 0。"""
    assert diagnose_positions([])["position_count"] == 0


def test_diagnose_max_weight_alert():
    """最大单仓超 40% 触发集中度告警。"""
    pos = [
        {"market_value": 600, "market": "CN", "strategy_code": "a"},
        {"market_value": 400, "market": "CN", "strategy_code": "b"},
    ]
    r = diagnose_positions(pos)
    assert r["max_weight"] == 0.6
    assert any("集中度" in a for a in r["alerts"])


def test_diagnose_by_market_distribution():
    """按市场聚合市值正确。"""
    pos = [
        {"market_value": 500, "market": "CN"},
        {"market_value": 500, "market": "US"},
    ]
    r = diagnose_positions(pos)
    assert r["by_market"]["CN"] == 500 and r["by_market"]["US"] == 500
    assert r["total_market_value"] == 1000


def test_diagnose_unrealized_pnl_sum():
    """浮动盈亏汇总正确。"""
    pos = [
        {"market_value": 100, "unrealized_pnl": 12.5},
        {"market_value": 100, "unrealized_pnl": -4.0},
    ]
    assert diagnose_positions(pos)["total_unrealized_pnl"] == 8.5
