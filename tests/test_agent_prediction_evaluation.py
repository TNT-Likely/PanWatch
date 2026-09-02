"""Agent 建议后验评估的交易日与分组语义。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.web.models  # noqa: F401  注册 ORM 模型
from src.web.database import Base


def _bar(day: str, close: float):
    return SimpleNamespace(date=day, close=close)


def test_friday_prediction_one_trading_day_uses_monday_close():
    """周五建议的 1 个交易日结果取周一收盘，而非周六。"""
    from src.core.prediction_outcome import _find_close_after_n_trading_days

    bars = [_bar("2026-08-28", 10), _bar("2026-08-31", 11)]

    assert _find_close_after_n_trading_days(bars, date(2026, 8, 28), 1) == 11


def test_two_horizons_saved_for_one_suggestion_share_group_id(monkeypatch):
    """同一次建议的 1/5 个交易日记录必须共用 group ID。"""
    from src.core.context_store import save_agent_prediction_outcome
    from src.web.database import Base
    from src.web.models import AgentPredictionOutcome

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr("src.core.context_store.SessionLocal", lambda: session)

    try:
        for horizon in (1, 5):
            assert save_agent_prediction_outcome(
                agent_name="daily_report",
                stock_symbol="600000",
                stock_market="CN",
                prediction_date="2026-08-28",
                horizon_days=horizon,
                action="buy",
                action_label="买入",
                prediction_group_id="group-1",
            )

        rows = session.query(AgentPredictionOutcome).order_by(AgentPredictionOutcome.horizon_days).all()
        assert [row.prediction_group_id for row in rows] == ["group-1", "group-1"]
        assert [row.horizon_unit for row in rows] == ["trading_days", "trading_days"]
    finally:
        session.close()


def _outcome(
    group_id: str,
    horizon: int,
    return_pct: float | None,
    *,
    action: str = "buy",
    status: str = "evaluated",
    unit: str = "trading_days",
):
    return SimpleNamespace(
        id=horizon,
        prediction_group_id=group_id,
        agent_name="daily_report",
        stock_symbol="600000",
        stock_market="CN",
        prediction_date="2026-08-28",
        horizon_days=horizon,
        horizon_unit=unit,
        action=action,
        action_label="买入",
        confidence=0.8,
        trigger_price=10.0,
        outcome_price=10.0 if return_pct is None else 10.0 * (1 + return_pct / 100),
        outcome_return_pct=return_pct,
        outcome_status=status,
        meta={"reason": "测试理由", "signal": "测试信号"},
        evaluated_at=None,
        created_at=None,
    )


def test_classify_prediction_hit_matches_declared_policy():
    """买卖方向与观望横盘阈值由后端统一判定。"""
    from src.core.agent_prediction_evaluation import classify_prediction_hit

    assert classify_prediction_hit("add", 0.01) is True
    assert classify_prediction_hit("reduce", -0.01) is True
    assert classify_prediction_hit("watch", 1.99) is True
    assert classify_prediction_hit("watch", 2.0) is False
    assert classify_prediction_hit("unknown", 1.0) is None


def test_group_prediction_outcomes_pivots_one_and_five_days():
    """同组 1/5 个交易日结果在前端只占一行。"""
    from src.core.agent_prediction_evaluation import group_prediction_outcomes

    groups = group_prediction_outcomes(
        [_outcome("group-1", 1, 1.2), _outcome("group-1", 5, -2.0)]
    )

    assert len(groups) == 1
    assert groups[0]["prediction_group_id"] == "group-1"
    assert groups[0]["outcomes"]["1"]["hit"] is True
    assert groups[0]["outcomes"]["5"]["hit"] is False


def test_legacy_same_day_suggestions_are_not_merged():
    """旧数据中同日同方向的两次建议仍必须是两条复盘。"""
    from src.core.agent_prediction_evaluation import group_prediction_outcomes

    first_created_at = datetime(2026, 8, 28, 9, 0, 0)
    second_created_at = first_created_at + timedelta(minutes=30)
    def legacy_row(record_id: int, horizon: int, return_pct: float, created_at: datetime):
        payload = vars(_outcome("", horizon, return_pct)).copy()
        payload.update(id=record_id, prediction_group_id=None, created_at=created_at)
        return SimpleNamespace(**payload)

    rows = [
        legacy_row(10, 1, 1.0, first_created_at),
        legacy_row(11, 5, 2.0, first_created_at),
        legacy_row(12, 1, -1.0, second_created_at),
        legacy_row(13, 5, -2.0, second_created_at),
    ]

    groups = group_prediction_outcomes(rows)

    assert len(groups) == 2
    assert {group["outcomes"]["1"]["return_pct"] for group in groups} == {1.0, -1.0}


def test_legacy_horizons_saved_across_seconds_stay_in_one_group():
    """旧写入将 1/5 日分开提交时，即使跨秒也仍属于同一建议。"""
    from src.core.agent_prediction_evaluation import group_prediction_outcomes

    first_created_at = datetime(2026, 8, 28, 9, 0, 0)
    one_day = vars(_outcome("", 1, 1.0)).copy()
    one_day.update(prediction_group_id=None, id=100, created_at=first_created_at)
    five_day = vars(_outcome("", 5, 2.0)).copy()
    five_day.update(
        prediction_group_id=None,
        id=101,
        created_at=first_created_at + timedelta(seconds=1),
    )

    groups = group_prediction_outcomes(
        [SimpleNamespace(**one_day), SimpleNamespace(**five_day)]
    )

    assert len(groups) == 1
    assert set(groups[0]["outcomes"]) == {"1", "5"}


def test_interleaved_legacy_writes_are_not_cross_paired():
    """同一旧分组键的并发写入宁可拆开，也不能错误交叉配对。"""
    from src.core.agent_prediction_evaluation import group_prediction_outcomes

    created_at = datetime(2026, 8, 28, 9, 0, 0)
    rows = []
    for record_id, horizon, return_pct in (
        (200, 1, 1.0), (201, 1, -1.0), (202, 5, 5.0), (203, 5, -5.0),
    ):
        payload = vars(_outcome("", horizon, return_pct)).copy()
        payload.update(
            id=record_id,
            prediction_group_id=None,
            created_at=created_at + timedelta(seconds=record_id - 200),
        )
        rows.append(SimpleNamespace(**payload))

    groups = group_prediction_outcomes(rows)

    assert len(groups) == 4
    assert all(len(group["outcomes"]) == 1 for group in groups)


def test_summary_marks_less_than_twenty_completed_samples_insufficient():
    """不足 20 个完成样本时不能包装成稳定命中率。"""
    from src.core.agent_prediction_evaluation import (
        group_prediction_outcomes,
        summarize_prediction_groups,
    )

    groups = group_prediction_outcomes(
        [_outcome(f"group-{index}", 5, 1.0) for index in range(19)]
    )

    assert summarize_prediction_groups(groups)["insufficient_sample"] is True
