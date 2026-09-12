"""因子自校准(M2):纯函数 compute_target / blend + DB 入口 calibrate_factor_weights。"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models  # noqa: F401  注册 ORM 模型
from src.platform.persistence.database import Base


def _mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# --------------------------- 纯函数:compute_target ---------------------------

def test_compute_target_additive_positive_ir():
    """加分因子 + 正 IR:目标权重 > 1(IR 优先,clamp 到上限 1.4)。"""
    from src.modules.strategy.factor_calibration import compute_target

    # ir=0.55 → term=1.1 → clamp 1.0 → 1 + 0.4*1.0 = 1.4
    assert abs(compute_target("catalyst_score", ic=0.06, ir=0.55) - 1.4) < 1e-9


def test_compute_target_falls_back_to_ic_when_no_ir():
    """IR 缺失时 fallback 用 IC。"""
    from src.modules.strategy.factor_calibration import compute_target

    # ir=None, ic=0.025 → term=0.5 → 1 + 0.4*0.5 = 1.2
    assert abs(compute_target("alpha_score", ic=0.025, ir=None) - 1.2) < 1e-9


def test_compute_target_penalty_good_negative_ic_raises_weight():
    """惩罚因子 IC 为负(惩罚有效)→ 翻符号后提权。"""
    from src.modules.strategy.factor_calibration import compute_target

    # risk_penalty ir=-0.5 → term=-1.0 → 惩罚翻符号 +1.0 → 1.4
    assert abs(compute_target("risk_penalty", ic=-0.04, ir=-0.5) - 1.4) < 1e-9


def test_compute_target_penalty_failing_positive_ic_lowers_weight():
    """惩罚因子 IC 翻正(惩罚失效)→ 翻符号后降权。"""
    from src.modules.strategy.factor_calibration import compute_target

    # risk_penalty ir=+0.5 → term=1.0 → 翻符号 -1.0 → 1 - 0.4 = 0.6
    assert abs(compute_target("risk_penalty", ic=0.04, ir=0.5) - 0.6) < 1e-9


def test_compute_target_returns_none_when_no_ic_ir():
    """IC、IR 均缺失 → 信息不足,返回 None(跳过该因子)。"""
    from src.modules.strategy.factor_calibration import compute_target

    assert compute_target("alpha_score", ic=None, ir=None) is None


# --------------------------- 纯函数:blend ---------------------------

def test_blend_ema():
    """EMA 平滑:blend(1.0, 1.4, alpha=0.35) = 1.14。"""
    from src.modules.strategy.factor_calibration import blend

    assert abs(blend(1.0, 1.4, alpha=0.35) - 1.14) < 1e-9


def test_blend_clamps_high():
    """混合结果超上限被 clamp 到 hi。"""
    from src.modules.strategy.factor_calibration import blend

    assert blend(1.45, 2.0, alpha=0.35, lo=0.5, hi=1.5) == 1.5


def test_blend_clamps_low():
    """混合结果低于下限被 clamp 到 lo。"""
    from src.modules.strategy.factor_calibration import blend

    assert blend(0.55, 0.0, alpha=0.35, lo=0.5, hi=1.5) == 0.5


# --------------------------- DB:evaluate_factor_ic 市场过滤/防泄漏 ---------------------------

def _seed_pair(db, sid, *, market, snapshot_date, alpha=0.0, ret=0.0,
               horizon=5, status="evaluated"):
    """插入一对 StrategyFactorSnapshot + StrategyOutcome(按 signal_run_id 关联)。"""
    from src.platform.persistence.models import StrategyFactorSnapshot, StrategyOutcome

    db.add(StrategyFactorSnapshot(
        signal_run_id=sid, snapshot_date=snapshot_date, stock_symbol=f"S{sid}",
        stock_market=market, strategy_code="trend_follow",
        alpha_score=alpha, final_score=50.0,
    ))
    db.add(StrategyOutcome(
        signal_run_id=sid, strategy_code="trend_follow", stock_symbol=f"S{sid}",
        stock_market=market, snapshot_date=snapshot_date, horizon_days=horizon,
        target_date=snapshot_date, outcome_return_pct=ret, outcome_status=status,
    ))


def _old_date(days_ago=30):
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_evaluate_factor_ic_filters_by_market():
    """传 market 时只统计该市场的样本。"""
    from src.modules.strategy.factor_eval import evaluate_factor_ic

    db = _mem_db()
    try:
        d = _old_date()
        for i in range(1, 6):  # 5 CN
            _seed_pair(db, i, market="CN", snapshot_date=d, alpha=float(i), ret=float(i))
        for i in range(6, 10):  # 4 HK
            _seed_pair(db, i, market="HK", snapshot_date=d, alpha=float(i), ret=float(i))
        db.commit()

        res = evaluate_factor_ic(days=90, horizon=5, min_samples=3, market="CN", db=db)
        assert res["factors"]["alpha_score"]["sample_size"] == 5
    finally:
        db.close()


def test_evaluate_factor_ic_excludes_unelapsed_horizon():
    """持有期未走完的样本(快照=今天)即使被标 evaluated 也被排除(防泄漏)。"""
    from src.modules.strategy.factor_eval import evaluate_factor_ic

    db = _mem_db()
    try:
        for i in range(1, 5):  # 4 条足够老
            _seed_pair(db, i, market="CN", snapshot_date=_old_date(), alpha=float(i), ret=float(i))
        # 1 条「今天」的泄漏样本
        _seed_pair(db, 99, market="CN", snapshot_date=date.today().strftime("%Y-%m-%d"),
                   alpha=9.0, ret=9.0)
        db.commit()

        res = evaluate_factor_ic(days=90, horizon=5, min_samples=3, market="CN", db=db)
        assert res["factors"]["alpha_score"]["sample_size"] == 4
    finally:
        db.close()


# --------------------------- DB:calibrate_factor_weights ---------------------------

def test_calibrate_moves_weight_from_ic_and_audits():
    """alpha 与收益完全正相关 → IC=+1 → 权重上调并写 auto 审计。"""
    from src.modules.strategy.factor_calibration import calibrate_factor_weights
    from src.platform.persistence.models import FactorWeight, FactorWeightHistory

    db = _mem_db()
    try:
        d = _old_date()
        for i in range(1, 7):  # 6 条,alpha 与 ret 单调一致
            _seed_pair(db, i, market="CN", snapshot_date=d, alpha=float(i), ret=float(i))
        db.commit()

        calibrate_factor_weights("CN", min_samples=5, db=db)

        row = db.query(FactorWeight).filter_by(factor_code="alpha_score", market="CN").first()
        assert row.weight > 1.0
        assert abs(row.weight - 1.14) < 0.02

        hist = (db.query(FactorWeightHistory)
                .filter_by(factor_code="alpha_score", market="CN", reason="auto").all())
        assert len(hist) == 1
        assert hist[0].ic is not None
    finally:
        db.close()


def test_calibrate_skips_pinned():
    """已 pin 的因子即使有强 IC 也不动。"""
    from src.modules.strategy.factor_calibration import calibrate_factor_weights
    from src.platform.persistence.models import FactorWeight

    db = _mem_db()
    try:
        db.add(FactorWeight(factor_code="alpha_score", market="CN", weight=1.0, is_pinned=True))
        db.commit()
        d = _old_date()
        for i in range(1, 7):
            _seed_pair(db, i, market="CN", snapshot_date=d, alpha=float(i), ret=float(i))
        db.commit()

        calibrate_factor_weights("CN", min_samples=5, db=db)

        row = db.query(FactorWeight).filter_by(factor_code="alpha_score", market="CN").first()
        assert row.weight == 1.0
    finally:
        db.close()
