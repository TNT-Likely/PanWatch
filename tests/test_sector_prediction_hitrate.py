"""板块预测命中率:命中判定纯逻辑 + hit-rate API(内存库,离线可跑)。

判定口径与 docs/premarket-engine.md 校准章节一致:
- 看多(bullish)预测按 momentum_score 降序取 top3,对照同日快照
  change_pct 降序 top3,进入即命中;
- 三价触及率 = agent_prediction_outcomes 按 horizon 聚合 outcome_status 分布。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    AgentPredictionOutcome,
    SectorPrediction,
    SectorSnapshot,
)


@pytest.fixture
def db():
    import src.platform.persistence.models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()
    engine.dispose()


# ────────────────────────── 替身工厂 ──────────────────────────


def _pred(day, code, momentum=None, confidence=None, direction="bullish", name=""):
    return SimpleNamespace(
        snapshot_date=day,
        board_code=code,
        board_name=name or f"板块{code}",
        direction=direction,
        confidence=confidence,
        momentum_score=momentum,
    )


def _snap(day, code, change_pct, name=""):
    return SimpleNamespace(
        snapshot_date=day,
        board_code=code,
        board_name=name or f"板块{code}",
        change_pct=change_pct,
    )


# ────────────────────────── 纯逻辑:top 选取 ──────────────────────────


def test_top_bullish_predictions_filters_and_ranks():
    """只取看多;按动量降序;momentum 缺失沉底;截断 top_n。"""
    from src.modules.market.sector_prediction_hitrate import top_bullish_predictions

    rows = [
        _pred("2026-09-22", "BK4", momentum=0.5),
        _pred("2026-09-22", "BK1", momentum=3.0),
        _pred("2026-09-22", "BK2", direction="bearish", momentum=9.0),  # 非看多,剔除
        _pred("2026-09-22", "BK3"),  # 动量缺失
        _pred("2026-09-22", "BK5", momentum=1.0),
    ]
    picks = top_bullish_predictions(rows, top_n=3)
    assert [p["board_code"] for p in picks] == ["BK1", "BK5", "BK4"]

    # 同动量按 confidence 降序,再按 board_code 稳定
    rows_tie = [
        _pred("2026-09-22", "BKb", momentum=2.0, confidence=0.5),
        _pred("2026-09-22", "BKa", momentum=2.0, confidence=0.9),
        _pred("2026-09-22", "BKc", momentum=2.0, confidence=None),
    ]
    picks = top_bullish_predictions(rows_tie, top_n=3)
    assert [p["board_code"] for p in picks] == ["BKa", "BKb", "BKc"]


def test_actual_top_boards_excludes_missing_change_pct():
    """实际涨幅榜:change_pct 缺失的板块不参与排名。"""
    from src.modules.market.sector_prediction_hitrate import actual_top_boards

    rows = [
        _snap("2026-09-22", "BK1", 1.0),
        _snap("2026-09-22", "BK2", None),
        _snap("2026-09-22", "BK3", 2.5),
        _snap("2026-09-22", "BK4", -0.3),
    ]
    top = actual_top_boards(rows, top_n=2)
    assert [b["board_code"] for b in top] == ["BK3", "BK1"]
    assert top[0]["change_pct"] == 2.5


# ────────────────────────── 纯逻辑:命中判定与聚合 ──────────────────────────


def test_evaluate_hit_rate_hit_miss_and_missing_snapshot():
    """命中日/未命中日/缺快照日/无看多日四类样本的逐日明细与汇总。"""
    from src.modules.market.sector_prediction_hitrate import evaluate_sector_hit_rate

    preds = [
        # 09-21:预测 BK1/BK2,实际 top3 = BK1/BK7/BK8 → BK1 命中
        _pred("2026-09-21", "BK1", momentum=2.0),
        _pred("2026-09-21", "BK2", momentum=1.0),
        _pred("2026-09-21", "BK3", momentum=0.5),  # top3 之外
        # 09-22:预测 BK4/BK5,实际 top3 = BK7/BK8/BK9 → 全部未命中
        _pred("2026-09-22", "BK4", momentum=2.0),
        _pred("2026-09-22", "BK5", momentum=1.0),
        # 09-23:无看多预测(bearish)
        _pred("2026-09-23", "BK6", momentum=5.0, direction="bearish"),
    ]
    snaps = [
        _snap("2026-09-21", "BK7", 3.0),
        _snap("2026-09-21", "BK1", 2.0),
        _snap("2026-09-21", "BK8", 1.0),
        _snap("2026-09-21", "BK2", 0.5),  # 第 4 名,不算命中
        _snap("2026-09-22", "BK7", 2.0),
        _snap("2026-09-22", "BK8", 1.0),
        _snap("2026-09-22", "BK9", 0.5),
        # 09-23 有快照但无看多预测 → 不评估
        _snap("2026-09-23", "BK7", 2.0),
    ]
    # 09-24 只有预测没有快照 → 缺快照,不评估
    preds.append(_pred("2026-09-24", "BK7", momentum=1.0))

    result = evaluate_sector_hit_rate(preds, snaps, top_n=3)
    summary = result["summary"]
    assert summary["days_listed"] == 4
    assert summary["days_evaluated"] == 2
    assert summary["days_missing_snapshot"] == 1
    assert summary["days_no_bullish"] == 1
    # 09-21 评估 3 个 pick(BK1/BK2/BK3),BK1 命中;09-22 评估 2 个,全未命中
    assert summary["picks_total"] == 5
    assert summary["picks_hit"] == 1
    assert summary["hit_rate"] == 0.2
    assert summary["sample_size"] == 5
    assert summary["hit_days"] == 1
    assert summary["day_hit_rate"] == 0.5

    by_day = {d["snapshot_date"]: d for d in result["days"]}
    assert by_day["2026-09-21"]["hit"] is True
    assert [p["hit"] for p in by_day["2026-09-21"]["picks"]] == [True, False, False]
    assert [b["board_code"] for b in by_day["2026-09-21"]["actual_top"]] == ["BK7", "BK1", "BK8"]
    assert by_day["2026-09-22"]["hit"] is False
    assert by_day["2026-09-23"]["evaluated"] is False
    assert by_day["2026-09-24"]["evaluated"] is False


def test_evaluate_hit_rate_empty_inputs():
    """空输入:汇总归零,命中率 None(样本不足),不抛错。"""
    from src.modules.market.sector_prediction_hitrate import evaluate_sector_hit_rate

    result = evaluate_sector_hit_rate([], [])
    assert result["days"] == []
    assert result["summary"]["hit_rate"] is None
    assert result["summary"]["sample_size"] == 0


def test_aggregate_three_price_status_distribution_and_hits():
    """按 horizon 聚合 outcome_status 分布;命中只对 evaluated 行判定。"""
    from src.modules.market.sector_prediction_hitrate import (
        aggregate_three_price_status,
    )

    def _outcome(horizon, status, return_pct=None, action="buy"):
        return SimpleNamespace(
            horizon_days=horizon,
            outcome_status=status,
            action=action,
            outcome_return_pct=return_pct,
        )

    rows = [
        _outcome(1, "evaluated", 1.2),   # buy > 0 → 命中
        _outcome(1, "evaluated", -0.5),  # buy < 0 → 未命中
        _outcome(1, "pending"),
        _outcome(3, "evaluated", 0.0),   # buy = 0 → 未命中
        _outcome(5, "pending"),
        _outcome(10, "evaluated", 3.0, action="watch"),  # watch |x|<2 → 未命中
        _outcome(10, "evaluated", 1.0, action="watch"),  # watch |x|<2 → 命中
    ]
    result = aggregate_three_price_status(rows, horizons=(1, 3, 5, 10))
    horizons = result["horizons"]
    assert set(horizons) == {"1", "3", "5", "10"}
    assert horizons["1"]["total"] == 3
    assert horizons["1"]["status_counts"] == {"evaluated": 2, "pending": 1}
    assert horizons["1"]["evaluated"] == 2
    assert horizons["1"]["pending"] == 1
    assert horizons["1"]["hit_count"] == 1
    assert horizons["1"]["hit_rate"] == 0.5
    assert horizons["3"]["hit_rate"] == 0.0
    assert horizons["5"]["status_counts"] == {"pending": 1}
    assert horizons["5"]["hit_rate"] is None
    assert horizons["10"]["hit_count"] == 1
    # 标准 horizon 之外的行也归组,不丢弃
    extra = aggregate_three_price_status([_outcome(7, "pending")], horizons=(1, 3, 5, 10))
    assert extra["horizons"]["7"]["total"] == 1


# ────────────────────────── HTTP 层(直调路由函数) ──────────────────────────


def _seed_two_days(db):
    """09-21 命中日 + 09-22 未命中日。"""
    db.add_all(
        [
            SectorPrediction(snapshot_date="2026-09-21", board_code="BK1", board_name="半导体", direction="bullish", momentum_score=2.0, confidence=0.7),
            SectorPrediction(snapshot_date="2026-09-21", board_code="BK2", board_name="银行", direction="bullish", momentum_score=1.0, confidence=0.5),
            SectorPrediction(snapshot_date="2026-09-21", board_code="BK3", board_name="医药", direction="bearish", momentum_score=9.0),
            SectorPrediction(snapshot_date="2026-09-22", board_code="BK4", board_name="券商", direction="bullish", momentum_score=2.0),
            SectorPrediction(snapshot_date="2026-09-22", board_code="BK5", board_name="有色", direction="bullish", momentum_score=1.0),
            SectorSnapshot(snapshot_date="2026-09-21", board_code="BK1", board_name="半导体", change_pct=2.5),
            SectorSnapshot(snapshot_date="2026-09-21", board_code="BK7", board_name="煤炭", change_pct=3.0),
            SectorSnapshot(snapshot_date="2026-09-21", board_code="BK8", board_name="电力", change_pct=1.0),
            SectorSnapshot(snapshot_date="2026-09-21", board_code="BK9", board_name="汽车", change_pct=0.2),
            SectorSnapshot(snapshot_date="2026-09-22", board_code="BK7", board_name="煤炭", change_pct=2.0),
            SectorSnapshot(snapshot_date="2026-09-22", board_code="BK8", board_name="电力", change_pct=1.0),
            SectorSnapshot(snapshot_date="2026-09-22", board_code="BK9", board_name="汽车", change_pct=0.5),
        ]
    )


def test_hit_rate_api_window_and_summary(db):
    """窗口过滤 + 命中汇总 + 逐日明细结构。"""
    from src.modules.market.api import sectors as sectors_api

    empty = sectors_api.sector_predictions_hit_rate(from_="2026-09-01", to="2026-09-07", db=db)
    assert empty["summary"]["days_evaluated"] == 0
    assert empty["summary"]["hit_rate"] is None
    assert empty["direction_set"] == ["bullish"]
    assert set(empty["three_price"]["horizons"]) == {"1", "3", "5", "10"}
    assert empty["three_price"]["agent_name"] == "premarket_pipeline"

    _seed_two_days(db)
    db.commit()

    result = sectors_api.sector_predictions_hit_rate(from_="2026-09-21", to="2026-09-22", db=db)
    assert result["window"] == {"from": "2026-09-21", "to": "2026-09-22"}
    assert result["top_n"] == 3
    summary = result["summary"]
    # 09-21:BK1 进实际 top3(煤炭3.0/半导体2.5/电力1.0)→ 命中;09-22 两票全不中
    assert summary["days_evaluated"] == 2
    assert summary["picks_total"] == 4
    assert summary["picks_hit"] == 1
    assert summary["hit_rate"] == 0.25
    days = {d["snapshot_date"]: d for d in result["days"]}
    assert days["2026-09-21"]["hit"] is True
    assert days["2026-09-22"]["hit"] is False

    # top_n=1:每天只取动量第 1 的看多预测;09-21 实际涨幅第 1 是煤炭(3.0),
    # 半导体排第 2 → 顶部单票不再命中(验证 top_n 收紧会如实降低命中)
    narrow = sectors_api.sector_predictions_hit_rate(from_="2026-09-21", to="2026-09-22", top_n=1, db=db)
    assert narrow["summary"]["picks_total"] == 2
    assert narrow["summary"]["picks_hit"] == 0
    assert narrow["summary"]["hit_rate"] == 0.0


def test_hit_rate_api_default_window_and_three_price(db):
    """to 缺省取库内最新预测日,from 缺省回看 30 天;三价只统计本 agent。"""
    from src.modules.market.api import sectors as sectors_api

    _seed_two_days(db)
    db.add_all(
        [
            # 流水线三价回评:horizon 1 命中 1/2,horizon 3 全 pending
            AgentPredictionOutcome(
                agent_name="premarket_pipeline", stock_symbol="600001", stock_market="CN",
                prediction_date="2026-09-21", horizon_days=1, outcome_status="evaluated",
                action="buy", outcome_return_pct=1.5,
            ),
            AgentPredictionOutcome(
                agent_name="premarket_pipeline", stock_symbol="600002", stock_market="CN",
                prediction_date="2026-09-21", horizon_days=1, outcome_status="evaluated",
                action="buy", outcome_return_pct=-0.8,
            ),
            AgentPredictionOutcome(
                agent_name="premarket_pipeline", stock_symbol="600001", stock_market="CN",
                prediction_date="2026-09-21", horizon_days=3, outcome_status="pending",
                action="buy",
            ),
            # 其他 agent 的记录不计入三价
            AgentPredictionOutcome(
                agent_name="premarket_outlook", stock_symbol="600003", stock_market="CN",
                prediction_date="2026-09-21", horizon_days=1, outcome_status="evaluated",
                action="buy", outcome_return_pct=9.9,
            ),
            # 窗口外(早于 from)不计入
            AgentPredictionOutcome(
                agent_name="premarket_pipeline", stock_symbol="600004", stock_market="CN",
                prediction_date="2026-08-01", horizon_days=1, outcome_status="evaluated",
                action="buy", outcome_return_pct=9.9,
            ),
        ]
    )
    db.commit()

    result = sectors_api.sector_predictions_hit_rate(db=db)
    assert result["window"]["to"] == "2026-09-22"  # 库内最新预测日
    assert result["window"]["from"] == "2026-08-23"  # to 前推 30 天

    horizons = result["three_price"]["horizons"]
    assert horizons["1"]["total"] == 2
    assert horizons["1"]["status_counts"] == {"evaluated": 2}
    assert horizons["1"]["hit_count"] == 1
    assert horizons["1"]["hit_rate"] == 0.5
    assert horizons["3"]["total"] == 1
    assert horizons["3"]["status_counts"] == {"pending": 1}
    assert horizons["3"]["hit_rate"] is None
    assert horizons["5"]["total"] == 0
    assert horizons["10"]["total"] == 0


def test_hit_rate_api_param_validation(db):
    """非法日期 / from 晚于 to → 400。"""
    from src.modules.market.api import sectors as sectors_api

    with pytest.raises(HTTPException) as exc:
        sectors_api.sector_predictions_hit_rate(from_="bad", db=db)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc2:
        sectors_api.sector_predictions_hit_rate(to="2026/09/01", db=db)
    assert exc2.value.status_code == 400
    with pytest.raises(HTTPException) as exc3:
        sectors_api.sector_predictions_hit_rate(from_="2026-09-10", to="2026-09-01", db=db)
    assert exc3.value.status_code == 400


def test_hit_rate_route_mounted_protected():
    """应用装配:hit-rate 路由已挂载在 /api/sectors 前缀且保持 protected。"""
    from src.bootstrap.application import app

    found = []
    for route in app.routes:
        ctx = getattr(route, "include_context", None)
        if ctx is not None:
            prefix = str(getattr(ctx, "prefix", "") or "")
            for sub in getattr(getattr(route, "original_router", None), "routes", []):
                path = prefix + str(getattr(sub, "path", ""))
                if path == "/api/sectors/predictions/hit-rate":
                    found.append(list(getattr(ctx, "dependencies", []) or []))
        else:
            path = getattr(route, "path", "")
            if path == "/api/sectors/predictions/hit-rate":
                found.append(list(getattr(route, "dependencies", []) or []))
    assert found, "缺少挂载路由: /api/sectors/predictions/hit-rate"
    assert any("get_current_user" in str(d) for d in found[0]), "hit-rate 未保持 protected"
