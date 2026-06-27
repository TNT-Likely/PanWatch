"""长线投资计划评估与建议纪律测试。"""

from src.core.long_term_plan import (
    apply_long_term_discipline,
    evaluate_add_plan,
    normalize_investment_profile,
)


def test_normalize_requires_defaults():
    """未配置时应返回默认关闭的长线计划。"""
    profile = normalize_investment_profile(None)
    assert profile["long_term_enabled"] is False
    assert profile["portfolio_role"] == "watch"
    assert len(profile["add_plan"]["levels"]) == 3


def test_evaluate_blocks_without_max_weight():
    """启用长线但未设最大仓位时不得建议加仓。"""
    profile = normalize_investment_profile(
        {"long_term_enabled": True, "portfolio_role": "core", "max_weight_pct": None}
    )
    result = evaluate_add_plan(
        profile,
        current_price=90,
        avg_cost=100,
        position_value=10000,
        total_assets=100000,
        available_cash=50000,
        has_buy_today=False,
        market="CN",
    )
    assert result["eligible"] is False
    assert "未设置最大仓位" in result["blockers"][0]


def test_evaluate_triggers_planned_add():
    """跌破加仓档位且未超仓位上限时应给出计划内加仓。"""
    profile = normalize_investment_profile(
        {
            "long_term_enabled": True,
            "portfolio_role": "core",
            "max_weight_pct": 25,
            "target_weight_pct": 15,
        }
    )
    result = evaluate_add_plan(
        profile,
        current_price=92,
        avg_cost=100,
        position_value=5000,
        total_assets=100000,
        available_cash=50000,
        has_buy_today=False,
        market="US",
    )
    assert result["triggered_level"] is not None
    assert result["eligible"] is True
    assert result["suggested_amount"] > 0
    assert result["suggested_qty"] >= 1


def test_evaluate_blocks_when_at_max_weight():
    """已达最大仓位时不应继续建议加仓。"""
    profile = normalize_investment_profile(
        {"long_term_enabled": True, "portfolio_role": "core", "max_weight_pct": 20}
    )
    result = evaluate_add_plan(
        profile,
        current_price=90,
        avg_cost=100,
        position_value=25000,
        total_assets=100000,
        available_cash=10000,
        has_buy_today=False,
        market="CN",
    )
    assert result["eligible"] is False
    assert any("最大仓位" in b for b in result["blockers"])


def test_evaluate_blocks_same_day_buy():
    """今日已有买入时不应重复建议加仓。"""
    profile = normalize_investment_profile(
        {"long_term_enabled": True, "portfolio_role": "core", "max_weight_pct": 30}
    )
    result = evaluate_add_plan(
        profile,
        current_price=90,
        avg_cost=100,
        position_value=10000,
        total_assets=100000,
        available_cash=50000,
        has_buy_today=True,
        market="CN",
    )
    assert result["eligible"] is False
    assert any("今日已有买入" in b for b in result["blockers"])


def test_core_discipline_downgrades_reduce():
    """核心仓不应因短线信号被建议减仓。"""
    profile = normalize_investment_profile(
        {"long_term_enabled": True, "portfolio_role": "core", "max_weight_pct": 25}
    )
    out = apply_long_term_discipline(
        {"action": "reduce", "action_label": "减仓", "reason": "MACD死叉", "should_alert": True},
        profile=profile,
    )
    assert out["action"] == "hold"
    assert out["should_alert"] is False


def test_core_discipline_allows_planned_add():
    """核心仓触发计划档位且评估通过时不应被降级。"""
    profile = normalize_investment_profile(
        {"long_term_enabled": True, "portfolio_role": "core", "max_weight_pct": 30}
    )
    add_eval = evaluate_add_plan(
        profile,
        current_price=90,
        avg_cost=100,
        position_value=5000,
        total_assets=100000,
        available_cash=50000,
        has_buy_today=False,
        market="US",
    )
    out = apply_long_term_discipline(
        {"action": "add", "action_label": "加仓", "reason": "触发计划档位", "should_alert": True},
        profile=profile,
        add_eval=add_eval,
    )
    assert out["action"] == "add"


def test_satellite_reduce_marks_scope():
    """卫星仓减仓建议应标注仅作用于卫星仓。"""
    profile = normalize_investment_profile(
        {"long_term_enabled": True, "portfolio_role": "satellite", "max_weight_pct": 15}
    )
    out = apply_long_term_discipline(
        {"action": "reduce", "action_label": "减仓", "signal": "RSI超买", "should_alert": True},
        profile=profile,
    )
    assert "卫星" in (out.get("signal") or "")
