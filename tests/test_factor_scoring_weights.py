"""评分集成(M3):_compute_factor_breakdown 按每因子权重加权 + 零回归。"""

from __future__ import annotations

from src.modules.strategy.strategy_engine import _compute_factor_breakdown
from src.platform.persistence.models import EntryCandidate


def _candidate(**kw):
    """构造一个内存 EntryCandidate(不入库),score=80 → 原始 alpha_score=13.5。"""
    defaults = dict(
        score=80.0, action="watch", status="active", plan_quality=80,
        candidate_source="watchlist", is_holding_snapshot=True,
        signal="", reason="", source_agent="", entry_low=None, entry_high=None, meta=None,
    )
    defaults.update(kw)
    return EntryCandidate(**defaults)


def _bd(row, fw):
    return _compute_factor_breakdown(
        row=row, strategy_code="pullback", weight=1.0,
        risk_level="low", regime_info=None, factor_weights=fw,
    )


def test_factor_weights_none_equals_empty_zero_regression():
    """factor_weights = None / {} / 全 1.0 三者输出完全一致(零回归)。"""
    row = _candidate()
    a = _bd(row, None)
    b = _bd(row, {})
    c = _bd(row, {
        "alpha_score": 1.0, "catalyst_score": 1.0, "quality_score": 1.0,
        "risk_penalty": 1.0, "crowd_penalty": 1.0,
    })
    assert a["raw_score"] == b["raw_score"] == c["raw_score"]
    assert a["weighted_score"] == b["weighted_score"] == c["weighted_score"]


def test_factor_weight_boost_adds_one_raw_factor():
    """alpha 权重 1.0→2.0:raw_score 恰好多出一份原始 alpha_score;快照值不受权重影响。"""
    row = _candidate()
    base = _bd(row, {"alpha_score": 1.0})
    boosted = _bd(row, {"alpha_score": 2.0})

    # 快照存 raw 因子分(IC 测在 raw 上),不随权重变化
    assert boosted["alpha_score"] == base["alpha_score"]
    assert base["alpha_score"] > 0
    # 加权只体现在合成的 raw_score 上
    assert abs((boosted["raw_score"] - base["raw_score"]) - base["alpha_score"]) < 1e-6


def test_penalty_weight_increases_deduction():
    """惩罚因子权重升高 → 扣分更多 → raw_score 更低。"""
    # 制造非零 risk_penalty:status 非 active(+2.5)
    row = _candidate(status="inactive")
    base = _bd(row, {"risk_penalty": 1.0})
    heavier = _bd(row, {"risk_penalty": 2.0})
    assert base["risk_penalty"] > 0
    assert heavier["raw_score"] < base["raw_score"]
