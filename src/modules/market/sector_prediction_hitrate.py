"""板块预测命中率评估:纯计算逻辑(API 与影子周报共用,不碰 DB)。

这里集中维护「什么算命中」,调用方(sectors API / pipeline_shadow_report 脚本)
不自行复制规则,与 ``agent_prediction_evaluation`` 同思路:

- 板块方向命中:逐 snapshot_date 取方向为看多的预测,按 momentum_score 降序取
  top_n,对照同日 ``SectorSnapshot.change_pct`` 降序 top_n;预测板块进入实际
  top_n 即命中(板块粒度),当日任一板块命中即该日命中(天粒度);
- 三价信号复盘:按 horizon 聚合 ``agent_prediction_outcomes.outcome_status``
  分布,已评估行的方向命中沿用 ``classify_prediction_hit`` 判定。

方向词表说明:仓库 ``SectorPrediction.direction`` 只有 bullish/bearish/neutral
三档,没有 strong_up/up 分档;规格中的「strong_up/up」按仓库现状映射为
``UP_DIRECTIONS = {"bullish"}``(见 docs/premarket-engine.md 校准章节)。

全部函数只依赖 ``_field`` 取值,ORM 行、SimpleNamespace 与 dict 替身均可传入,离线可测。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from src.modules.automation.agent_prediction_evaluation import classify_prediction_hit

#: 计入「看多」预测的方向集合(仓库词表无 strong_up 分档,bullish 即看多)
UP_DIRECTIONS = frozenset({"bullish"})
#: 默认取预测/实际的 top N(规格口径 top3)
DEFAULT_TOP_N = 3
#: 三价复盘标准 horizon(交易日),与 premarket_config.PREDICTION_HORIZONS 对齐
DEFAULT_HORIZONS: tuple[int, ...] = (1, 3, 5, 10)


def _num(value: Any) -> float | None:
    """宽容取数:仅接受 int/float(含 0),其余(含 None/字符串)视为缺失。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _field(obj: Any, key: str) -> Any:
    """兼容映射与对象两种行替身(SimpleNamespace/ORM 行/dict)。"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _pred_sort_key(row: Any) -> tuple:
    """看多预测排序:momentum_score 降序 → confidence 降序 → board_code 升序。"""
    mom = _num(_field(row, "momentum_score"))
    conf = _num(_field(row, "confidence"))
    return (
        -(mom if mom is not None else float("-inf")),
        -(conf if conf is not None else float("-inf")),
        str(_field(row, "board_code") or ""),
    )


def _snapshot_sort_key(row: Any) -> tuple:
    """实际涨幅排序:change_pct 降序 → board_code 升序(缺失者沉底)。"""
    chg = _num(_field(row, "change_pct"))
    return (
        -(chg if chg is not None else float("-inf")),
        str(_field(row, "board_code") or ""),
    )


def top_bullish_predictions(pred_rows: Sequence[Any], top_n: int = DEFAULT_TOP_N) -> list[dict]:
    """看多预测按动量降序取 top_n,返回可序列化的 pick 列表。"""
    picks = []
    for row in pred_rows:
        if str(_field(row, "direction") or "").strip().lower() not in UP_DIRECTIONS:
            continue
        picks.append(
            {
                "board_code": str(_field(row, "board_code") or ""),
                "board_name": str(_field(row, "board_name") or ""),
                "direction": str(_field(row, "direction") or ""),
                "confidence": _num(_field(row, "confidence")),
                "momentum_score": _num(_field(row, "momentum_score")),
            }
        )
    picks.sort(key=_pred_sort_key)
    return picks[: max(1, int(top_n))]


def actual_top_boards(snapshot_rows: Sequence[Any], top_n: int = DEFAULT_TOP_N) -> list[dict]:
    """当日实际涨幅榜 top_n;change_pct 缺失的板块不参与排名。"""
    boards = [
        {
            "board_code": str(_field(row, "board_code") or ""),
            "board_name": str(_field(row, "board_name") or ""),
            "change_pct": _num(_field(row, "change_pct")),
        }
        for row in snapshot_rows
        if _num(_field(row, "change_pct")) is not None
    ]
    boards.sort(key=_snapshot_sort_key)
    return boards[: max(1, int(top_n))]


def evaluate_sector_hit_rate(
    pred_rows: Iterable[Any],
    snapshot_rows: Iterable[Any],
    *,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """逐 snapshot_date 评估预测 top_n vs 实际涨幅 top_n,返回逐日明细与汇总。

    - 当日无任何有效快照(change_pct 全缺失)→ 该日不评估,不计入样本;
    - 当日无看多预测 → 该日不评估,不计入样本;
    - 命中率主口径为板块粒度(picks_hit/picks_total),天粒度(day_hit_rate)并列输出。
    """
    top_n = max(1, int(top_n))
    preds_by_date: dict[str, list[Any]] = {}
    for row in pred_rows or []:
        day = str(_field(row, "snapshot_date") or "")
        if day:
            preds_by_date.setdefault(day, []).append(row)
    snaps_by_date: dict[str, list[Any]] = {}
    for row in snapshot_rows or []:
        day = str(_field(row, "snapshot_date") or "")
        if day:
            snaps_by_date.setdefault(day, []).append(row)

    days: list[dict] = []
    picks_total = picks_hit = 0
    days_evaluated = hit_days = 0
    days_missing_snapshot = days_no_bullish = 0

    for day in sorted(preds_by_date):
        pred_top = top_bullish_predictions(preds_by_date[day], top_n)
        if not pred_top:
            days_no_bullish += 1
            days.append(
                {
                    "snapshot_date": day,
                    "evaluated": False,
                    "reason": "当日无看多(bullish)预测",
                    "picks": [],
                    "actual_top": [],
                    "hit": None,
                }
            )
            continue
        actual_top = actual_top_boards(snaps_by_date.get(day) or [], top_n)
        if not actual_top:
            days_missing_snapshot += 1
            days.append(
                {
                    "snapshot_date": day,
                    "evaluated": False,
                    "reason": "缺当日板块快照(change_pct 全缺失)",
                    "picks": pred_top,
                    "actual_top": [],
                    "hit": None,
                }
            )
            continue
        actual_codes = {b["board_code"] for b in actual_top}
        pick_rows = []
        day_hit = False
        for p in pred_top:
            hit = p["board_code"] in actual_codes
            pick_rows.append({**p, "hit": hit})
            picks_total += 1
            picks_hit += int(hit)
            day_hit = day_hit or hit
        days_evaluated += 1
        hit_days += int(day_hit)
        days.append(
            {
                "snapshot_date": day,
                "evaluated": True,
                "reason": "",
                "picks": pick_rows,
                "actual_top": actual_top,
                "hit": day_hit,
            }
        )

    return {
        "summary": {
            "days_listed": len(days),
            "days_evaluated": days_evaluated,
            "days_missing_snapshot": days_missing_snapshot,
            "days_no_bullish": days_no_bullish,
            "hit_days": hit_days,
            "day_hit_rate": round(hit_days / days_evaluated, 4) if days_evaluated else None,
            "picks_total": picks_total,
            "picks_hit": picks_hit,
            "hit_rate": round(picks_hit / picks_total, 4) if picks_total else None,
            "sample_size": picks_total,
        },
        "days": days,
    }


def _empty_horizon_stat() -> dict:
    return {
        "total": 0,
        "status_counts": {},
        "evaluated": 0,
        "pending": 0,
        "hit_count": 0,
        "hit_rate": None,
    }


def aggregate_three_price_status(
    outcome_rows: Iterable[Any],
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> dict:
    """三价信号复盘:按 horizon 聚合 outcome_status 分布与方向命中率。

    - status_counts 覆盖全量行的 outcome_status 分布(pending/evaluated/...);
    - hit 仅对 status=evaluated 且可判定的行,按 ``classify_prediction_hit``
      (EVALUATION_POLICY)计算;盘中是否实际触及入场/止损/止盈价未落库,
      不可计算,诚实缺省(见 docs/premarket-engine.md)。
    """
    buckets: dict[str, dict] = {str(int(h)): _empty_horizon_stat() for h in horizons}
    for row in outcome_rows or []:
        try:
            horizon = str(max(1, int(_field(row, "horizon_days") or 1)))
        except (TypeError, ValueError):
            continue
        bucket = buckets.setdefault(horizon, _empty_horizon_stat())
        bucket["total"] += 1
        status = str(_field(row, "outcome_status") or "pending")
        bucket["status_counts"][status] = bucket["status_counts"].get(status, 0) + 1
        if status == "evaluated":
            bucket["evaluated"] += 1
            hit = classify_prediction_hit(
                str(_field(row, "action") or ""),
                _field(row, "outcome_return_pct"),
            )
            if hit is not None:
                bucket["hit_count"] += int(bool(hit))
    for bucket in buckets.values():
        bucket["pending"] = bucket["status_counts"].get("pending", 0)
        if bucket["evaluated"]:
            bucket["hit_rate"] = round(bucket["hit_count"] / bucket["evaluated"], 4)
    return {
        "horizons": dict(sorted(buckets.items(), key=lambda kv: int(kv[0]))),
    }
