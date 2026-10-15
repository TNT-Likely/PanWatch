"""Explainable release evidence, separate from the existence of a ranking.

Historical thresholds are minimum release checks, not investment guarantees.
Research publication never becomes validated publication through a read call.
"""

import math
from .allocation import PAPER_POLICY


def number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def assess(model, mode="research"):
    model = model or {}
    config, metrics = model.get("config", {}), model.get("metrics", {})
    backtest = metrics.get("backtest", {})
    reasons = []
    aligned = (
        backtest.get("allocation_policy") == PAPER_POLICY
        and backtest.get("paper_aligned") is True
    )
    if not aligned:
        reasons.append("qlib_strategy_backtest_mismatch")
    days, ic, ret, dd = (
        metrics.get("ic_dates"),
        metrics.get("rank_ic"),
        backtest.get("return"),
        backtest.get("max_drawdown"),
    )
    minimum_days = max(20, config.get("min_test_days", 20))
    minimum_ic = max(0, config.get("min_ic", 0))
    maximum_dd = min(0.3, config.get("max_drawdown", 0.3))

    def passed(report):
        bt = report.get("backtest", {})
        return (
            number(report.get("ic_dates"))
            and report["ic_dates"] >= minimum_days
            and number(report.get("rank_ic"))
            and report["rank_ic"] > minimum_ic
            and number(bt.get("return"))
            and bt["return"] > 0
            and number(bt.get("max_drawdown"))
            and 0 <= bt["max_drawdown"] <= maximum_dd
            and bt.get("allocation_policy") == PAPER_POLICY
            and bt.get("paper_aligned") is True
        )

    if not all(number(v) for v in (days, ic, ret, dd)) or (
        number(days) and days < minimum_days
    ):
        reasons.append("qlib_strategy_evidence_insufficient")
    if (
        (number(ic) and ic <= minimum_ic)
        or (number(ret) and ret <= 0)
        or (number(dd) and (dd < 0 or dd > maximum_dd))
    ):
        reasons.append("qlib_strategy_performance_failed")
    baseline = model.get("baseline", {})
    baseline_return = baseline.get("backtest", {}).get("return")
    if (
        not number(baseline_return)
        or not number(baseline.get("rank_ic"))
        or baseline.get("backtest", {}).get("allocation_policy") != PAPER_POLICY
        or baseline.get("backtest", {}).get("paper_aligned") is not True
    ):
        reasons.append("qlib_strategy_baseline_unverified")
    elif (
        number(ret)
        and number(ic)
        and (ret < baseline_return or ic < baseline["rank_ic"])
    ):
        reasons.append("qlib_strategy_baseline_not_beaten")
    folds = model.get("rolling", [])
    if len(folds) < 3 or any(not passed(f.get("metrics", {})) for f in folds):
        reasons.append("qlib_strategy_rolling_unverified")
    if (
        mode != "validated"
        or model.get("data_kind") != "market"
        or model.get("universe_kind") != "historical_membership"
        or model.get("adjustment") != "point_in_time"
        or model.get("price_basis", "raw_execution") != "raw_execution"
    ):
        reasons.append("qlib_strategy_research_only")
    return dict(
        status="eligible"
        if not reasons
        else "failed"
        if "qlib_strategy_performance_failed" in reasons
        else "research_only",
        eligible=not reasons,
        reasons=reasons,
        policy_aligned=aligned,
        rank_ic=ic if number(ic) else None,
        net_return=ret if number(ret) else None,
        max_drawdown=dd if number(dd) else None,
        test_days=backtest.get("days"),
        paper_policy=PAPER_POLICY,
        horizon=config.get("horizon", 5),
        topk=config.get("topk", 10),
    )
