from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.signals.a_share_decision import DecisionLabel, evaluate_a_share_decision
from src.web.api.watchlist import router


def _base_input(**overrides):
    data = {
        "symbol": "600519",
        "name": "贵州茅台",
        "market": "CN",
        "quote": {
            "current_price": 102.0,
            "change_pct": 2.4,
            "high_price": 103.0,
            "low_price": 99.0,
            "turnover": 1_200_000_000,
        },
        "technical": {
            "trend": "多头排列",
            "ma5": 101.0,
            "ma10": 100.0,
            "ma20": 98.0,
            "macd_cross": "金叉",
            "rsi6": 58.0,
            "kdj_j": 72.0,
            "volume_ratio": 1.8,
            "support": 98.0,
            "resistance": 112.0,
            "boll_status": "正常波动",
        },
        "position": {
            "has_position": False,
            "stop_loss": 97.0,
            "target_price": 112.0,
        },
        "sector_strength": 66.0,
    }
    data.update(overrides)
    return data


def test_buy_candidate_requires_structured_positive_conditions():
    """多项正向结构化条件满足时输出买入候选。"""
    result = evaluate_a_share_decision(_base_input())

    assert result.label == DecisionLabel.BUY_CANDIDATE
    assert result.score >= 72
    assert "跌破 MA20 或关键支撑" in result.invalidation_conditions
    assert any("风险收益比" in item for item in result.reasons)


def test_missing_stop_loss_blocks_buy_candidate():
    """没有止损位时即使技术面偏强也不得输出买入候选。"""
    data = _base_input(position={"has_position": False, "target_price": 112.0})

    result = evaluate_a_share_decision(data)

    assert result.label == DecisionLabel.WATCH
    assert any("未设置止损位" in item for item in result.risks)


def test_no_chase_overrides_buy_candidate():
    """涨幅过高或距离支撑过远时禁止追高优先于买入候选。"""
    data = _base_input(
        quote={
            "current_price": 111.0,
            "change_pct": 8.2,
            "high_price": 112.0,
            "low_price": 103.0,
        },
        technical={
            **_base_input()["technical"],
            "rsi6": 82.0,
            "kdj_j": 108.0,
            "support": 98.0,
            "resistance": 113.0,
        },
    )

    result = evaluate_a_share_decision(data)

    assert result.label == DecisionLabel.NO_CHASE
    assert result.risk_level == "高"
    assert any("涨幅" in item for item in result.reasons)


def test_stop_loss_has_highest_priority_for_position():
    """当前价触及止损价时直接输出止损触发。"""
    data = _base_input(
        quote={"current_price": 95.5, "change_pct": -4.3},
        position={"has_position": True, "avg_cost": 101.0, "quantity": 100, "stop_loss": 96.0},
    )

    result = evaluate_a_share_decision(data)

    assert result.label == DecisionLabel.STOP_LOSS
    assert result.score >= 90
    assert result.risk_level == "高"


def test_position_near_resistance_and_overheated_warns_reduce():
    """持仓接近压力位且动量过热时输出减仓警告。"""
    data = _base_input(
        quote={"current_price": 111.2, "change_pct": 5.4, "high_price": 112.0, "low_price": 105.0},
        position={"has_position": True, "avg_cost": 98.0, "quantity": 100, "stop_loss": 96.0},
        technical={**_base_input()["technical"], "rsi6": 70.0, "resistance": 112.0},
    )

    result = evaluate_a_share_decision(data)

    assert result.label == DecisionLabel.REDUCE_WARNING
    assert any("压力位" in item or "过热" in item for item in result.risks)


def test_watchlist_router_mounted_shape():
    """watchlist API 路由暴露手动评估入口并返回规则标签。"""
    paths = {route.path for route in router.routes}

    assert "/signals/evaluate" in paths

    app = FastAPI()
    app.include_router(router, prefix="/api/watchlist")
    client = TestClient(app)
    response = client.post("/api/watchlist/signals/evaluate", json=_base_input())

    assert response.status_code == 200
    assert response.json()["label"] == "买入候选"
