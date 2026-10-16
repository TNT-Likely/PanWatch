"""Controlled release evidence; these metrics are not market validation."""

import copy
import pytest
from src.modules.qlib_research import assessment, automation, service
from src.platform.persistence.qlib_models import QlibControl, QlibRecord
from tests.test_qlib_automation import ready, authorize
from tests.test_qlib_paper_plans import storage
from tests.test_qlib_service import storage as qlib_storage


def valid_model():
    metrics = dict(
        rank_ic=0.1,
        ic_dates=60,
        backtest=dict(
            days=65,
            **{"return": 0.2},
            max_drawdown=0.1,
            allocation_policy=assessment.PAPER_POLICY,
            paper_aligned=True,
        ),
    )
    return dict(
        config={},
        metrics=metrics,
        baseline=dict(
            rank_ic=0.05,
            backtest={
                "return": 0.1,
                "allocation_policy": assessment.PAPER_POLICY,
                "paper_aligned": True,
            },
        ),
        rolling=[dict(metrics=copy.deepcopy(metrics)) for _ in range(3)],
        data_kind="market",
        universe_kind="historical_membership",
        adjustment="point_in_time",
    )


@pytest.mark.parametrize(
    "case,reason",
    [
        ("legacy", "backtest_mismatch"),
        ("loss", "performance_failed"),
        ("ic", "performance_failed"),
        ("drawdown", "performance_failed"),
        ("missing", "evidence_insufficient"),
        ("baseline", "baseline_not_beaten"),
        ("fold", "rolling_unverified"),
        ("research", "research_only"),
        ("nan", "evidence_insufficient"),
        ("metadata", "research_only"),
    ],
)
def test_rank_is_not_release_evidence(case, reason):
    model = valid_model()
    mode = "validated"
    if case == "legacy":
        model["metrics"]["backtest"].pop("allocation_policy")
    if case == "loss":
        model["metrics"]["backtest"]["return"] = -0.3
    if case == "ic":
        model["metrics"]["rank_ic"] = -0.01
    if case == "drawdown":
        model["metrics"]["backtest"]["max_drawdown"] = 0.38
    if case == "missing":
        model["metrics"]["ic_dates"] = 19
    if case == "baseline":
        model["baseline"]["backtest"]["return"] = 0.3
    if case == "fold":
        model["rolling"][2]["metrics"]["backtest"]["return"] = -0.01
    if case == "research":
        mode = "research"
    if case == "nan":
        model["metrics"]["rank_ic"] = float("nan")
    if case == "metadata":
        model["adjustment"] = "snapshot_adjusted"
    result = assessment.assess(model, mode)
    assert not result["eligible"] and f"qlib_strategy_{reason}" in result["reasons"]


def test_complete_aligned_evidence_has_finite_readable_summary():
    result = assessment.assess(valid_model(), "validated")
    assert result["eligible"] and result["reasons"] == []
    assert result["net_return"] == 0.2 and result["policy_aligned"]


def test_current_negative_research_model_cannot_authorize_by_direct_api(storage, ready):
    _, model_id = ready
    with storage() as db:
        model = db.get(QlibRecord, model_id)
        payload = copy.deepcopy(model.payload)
        payload["metrics"]["backtest"]["return"] = -0.3
        model.payload = payload
        db.commit()
    assert not automation.status()["can_authorize"]
    with pytest.raises(ValueError, match="strategy_performance_failed"):
        automation.configure(authorize(model_id), "qa")
    with storage() as db:
        assert db.query(QlibRecord).filter_by(kind="paper_authorization").count() == 0
        assert db.query(QlibRecord).filter_by(kind="decision").count() == 0


def test_existing_grant_rechecks_release_mode_without_mutating_history(storage, ready):
    _, model_id = ready
    grant = automation.configure(authorize(model_id), "qa")
    with storage() as db:
        state = db.get(QlibControl, 1)
        state.settings = {**state.settings, "champion_modes": {"CN": "research"}}
        db.commit()
    result = automation.status()
    assert result["authorized"] and result["grant_id"] == grant["grant_id"]
    assert (
        result["phase"] == "blocked"
        and result["reason"] == "qlib_strategy_research_only"
    )
    assert automation.tick()["phase"] == "blocked"
