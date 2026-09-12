"""因子自校准闭环(M4):calibrate_all_markets 端到端接通评分。"""

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


def _seed_pair(db, sid, *, market, snapshot_date, alpha, ret, horizon=5):
    from src.platform.persistence.models import StrategyFactorSnapshot, StrategyOutcome

    db.add(StrategyFactorSnapshot(
        signal_run_id=sid, snapshot_date=snapshot_date, stock_symbol=f"S{sid}",
        stock_market=market, strategy_code="trend_follow",
        alpha_score=alpha, final_score=50.0,
    ))
    db.add(StrategyOutcome(
        signal_run_id=sid, strategy_code="trend_follow", stock_symbol=f"S{sid}",
        stock_market=market, snapshot_date=snapshot_date, horizon_days=horizon,
        target_date=snapshot_date, outcome_return_pct=ret, outcome_status="evaluated",
    ))


def test_calibrate_all_markets_closes_loop_into_scoring():
    """端到端:快照+outcome → calibrate_all_markets → CN alpha 权重上调 → 评分 raw_score 提升。"""
    from src.modules.strategy.factor_calibration import calibrate_all_markets
    from src.modules.strategy.factor_weights import get_factor_weights
    from src.modules.strategy.strategy_engine import _compute_factor_breakdown
    from src.platform.persistence.models import EntryCandidate

    db = _mem_db()
    try:
        d = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        for i in range(1, 7):  # CN:alpha 与 ret 完全正相关
            _seed_pair(db, i, market="CN", snapshot_date=d, alpha=float(i), ret=float(i))
        db.commit()

        res = calibrate_all_markets(db=db, min_samples=5)
        assert set(res) == {"CN", "HK", "US"}

        w = get_factor_weights("CN", db=db)
        assert w["alpha_score"] > 1.0  # IC 闭环把权重抬高

        row = EntryCandidate(
            score=80.0, action="watch", status="active", plan_quality=80,
            candidate_source="watchlist", is_holding_snapshot=True,
            signal="", reason="", source_agent="", meta=None,
        )
        kw = dict(row=row, strategy_code="pullback", weight=1.0,
                  risk_level="low", regime_info=None)
        base = _compute_factor_breakdown(**kw, factor_weights={"alpha_score": 1.0})
        boosted = _compute_factor_breakdown(**kw, factor_weights=w)
        assert boosted["raw_score"] > base["raw_score"]
    finally:
        db.close()
