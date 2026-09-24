#!/usr/bin/env python3
"""盘前决策流水线影子周报:命中率趋势 / outcomes 基线对比 / 数据源可用率。

用法:
    python scripts/pipeline_shadow_report.py                     # 最近 7 天,输出到 stdout
    python scripts/pipeline_shadow_report.py --days 14
    python scripts/pipeline_shadow_report.py --from 2026-09-14 --to 2026-09-20
    python scripts/pipeline_shadow_report.py --out reports/shadow-2026W38.md

内容(全部只读本地库,不访问外部接口):
1. 板块预测命中率:逐日预测 top3 vs 当日快照涨幅 top3(判定规则与
   GET /api/sectors/predictions/hit-rate 共用 sector_prediction_hitrate);
2. 三价信号复盘:premarket_pipeline 与 premarket_outlook(既有基线)按
   horizon 对比命中率(沿用 agent_prediction_evaluation 的评估政策);
3. 数据源可用率:从 analysis_history.raw_data.stages 聚合各 source 的
   ok 率与最近降级级别(管线运行明细落库位置,agent_runs 表无 raw_data 列)。

空数据段落显式标注「样本不足」,不编造任何数值(fail-soft)。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# 脚本直跑时把仓库根加入 sys.path,复用应用自身的持久化层
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.automation import premarket_config as pcfg  # noqa: E402
from src.modules.automation.agent_prediction_evaluation import (  # noqa: E402
    group_prediction_outcomes,
    summarize_prediction_groups,
)
from src.modules.market.sector_prediction_hitrate import (  # noqa: E402
    DEFAULT_TOP_N,
    aggregate_three_price_status,
    evaluate_sector_hit_rate,
)
from src.platform.persistence.database import SessionLocal, init_db  # noqa: E402
from src.platform.persistence.models import (  # noqa: E402
    AgentPredictionOutcome,
    AnalysisHistory,
    SectorPrediction,
    SectorSnapshot,
)
from src.platform.scheduling.timezone import beijing_now  # noqa: E402

logger = logging.getLogger("pipeline_shadow_report")

#: 缺省周报窗口(自然天)
DEFAULT_DAYS = 7
#: 基线 agent:接入流水线之前既有盘前建议的后验记录
BASELINE_AGENT_NAME = "premarket_outlook"


# ────────────────────────── 窗口解析 ──────────────────────────


def resolve_window(args: argparse.Namespace) -> tuple[str, str]:
    """--from/--to 显式优先;否则 end=今天、start=end 往前 --days+1 天。"""
    if args.to:
        end = date.fromisoformat(args.to)
    else:
        end = beijing_now().date()
    if args.from_:
        start = date.fromisoformat(args.from_)
    else:
        start = end - timedelta(days=max(1, int(args.days)) - 1)
    if start > end:
        raise SystemExit("--from 不能晚于 --to")
    return start.isoformat(), end.isoformat()


# ────────────────────────── 数据装载 ──────────────────────────


def load_sector_window(db, start: str, end: str) -> tuple[list, list]:
    """窗口内预测与快照全量行(分组/排序/命中判定在纯逻辑模块)。"""
    preds = (
        db.query(SectorPrediction)
        .filter(SectorPrediction.snapshot_date >= start, SectorPrediction.snapshot_date <= end)
        .all()
    )
    snaps = (
        db.query(SectorSnapshot)
        .filter(SectorSnapshot.snapshot_date >= start, SectorSnapshot.snapshot_date <= end)
        .all()
    )
    return preds, snaps


def load_outcomes_summary(db, start: str, end: str, agent_name: str) -> dict:
    """某 agent 窗口内建议组的后验汇总(summarize_prediction_groups 口径)。"""
    rows = (
        db.query(AgentPredictionOutcome)
        .filter(
            AgentPredictionOutcome.agent_name == agent_name,
            AgentPredictionOutcome.prediction_date >= start,
            AgentPredictionOutcome.prediction_date <= end,
        )
        .all()
    )
    return summarize_prediction_groups(group_prediction_outcomes(rows))


def load_pipeline_histories(db, start: str, end: str, agent_name: str) -> list:
    """窗口内流水线分析历史(raw_data.stages 是数据源可用率的唯一来源)。"""
    return (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == "*",
            AnalysisHistory.analysis_date >= start,
            AnalysisHistory.analysis_date <= end,
        )
        .order_by(AnalysisHistory.analysis_date.asc(), AnalysisHistory.id.asc())
        .all()
    )


# ────────────────────────── 聚合(纯函数,可离线单测) ──────────────────────────


def aggregate_source_availability(histories: list) -> dict:
    """从分析历史 raw_data.stages 聚合各 source 的 ok 率与最近降级级别。

    - 行内 raw_data 缺 stages / 结构不符 → 计入 runs_total 但跳过聚合(fail-soft);
    - ok 率 = ok 次数 / 出现次数(按 source 名聚合,跨阶段累计);
    - last_degrade_level 取该 source 最近一次出现的 degrade_level(缺失记 None)。
    """
    sources: dict[str, dict] = {}
    runs_total = len(histories)
    runs_with_stages = 0
    for hist in histories:
        raw = getattr(hist, "raw_data", None)
        stages = raw.get("stages") if isinstance(raw, dict) else None
        if not isinstance(stages, list) or not stages:
            continue
        runs_with_stages += 1
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            stage_name = str(stage.get("stage") or "")
            for src in stage.get("sources") or []:
                if not isinstance(src, dict):
                    continue
                name = str(src.get("name") or "")
                if not name:
                    continue
                bucket = sources.setdefault(
                    name, {"total": 0, "ok": 0, "ok_rate": None, "last_degrade_level": None, "stages": []}
                )
                bucket["total"] += 1
                bucket["ok"] += int(bool(src.get("ok")))
                level = src.get("degrade_level")
                if level is not None:
                    bucket["last_degrade_level"] = level
                if stage_name and stage_name not in bucket["stages"]:
                    bucket["stages"].append(stage_name)
    for bucket in sources.values():
        if bucket["total"]:
            bucket["ok_rate"] = round(bucket["ok"] / bucket["total"], 4)
        bucket["stages"] = ",".join(sorted(bucket["stages"]))
    return {
        "runs_total": runs_total,
        "runs_with_stages": runs_with_stages,
        "sources": dict(sorted(sources.items())),
    }


def build_baseline_comparison(pipeline: dict, baseline: dict) -> dict:
    """流水线 vs 既有基线(premarket_outlook)按 horizon 对比命中率。"""
    horizons: dict[str, dict] = {}
    pipe_h = pipeline.get("horizons") or {}
    base_h = baseline.get("horizons") or {}
    for key in sorted(set(pipe_h) | set(base_h), key=lambda k: int(k) if str(k).isdigit() else 999):
        p, b = pipe_h.get(key) or {}, base_h.get(key) or {}
        p_rate, b_rate = p.get("hit_rate"), b.get("hit_rate")
        delta = (
            round(p_rate - b_rate, 4)
            if isinstance(p_rate, (int, float)) and isinstance(b_rate, (int, float))
            else None
        )
        horizons[key] = {
            "pipeline_completed": p.get("completed_count", 0),
            "pipeline_hit_rate": p_rate,
            "baseline_completed": b.get("completed_count", 0),
            "baseline_hit_rate": b_rate,
            "delta_hit_rate": delta,
        }
    return {
        "pipeline_suggestion_count": pipeline.get("suggestion_count", 0),
        "baseline_suggestion_count": baseline.get("suggestion_count", 0),
        "pipeline_pending": pipeline.get("pending_count", 0),
        "baseline_pending": baseline.get("pending_count", 0),
        "pipeline_insufficient_sample": pipeline.get("insufficient_sample"),
        "baseline_insufficient_sample": baseline.get("insufficient_sample"),
        "horizons": horizons,
    }


# ────────────────────────── Markdown 渲染 ──────────────────────────


def _fmt_rate(rate) -> str:
    return f"{rate * 100:.1f}%" if isinstance(rate, (int, float)) else "样本不足"


def _fmt_board(pick: dict) -> str:
    mark = ""
    if isinstance(pick.get("hit"), bool):
        mark = "✅" if pick["hit"] else "❌"
    return f"{pick.get('board_name') or pick.get('board_code')}{mark}"


def render_markdown(report: dict) -> str:
    """把聚合结果渲染为周报 Markdown;空数据段落显式标注,不编造数值。"""
    window = report["window"]
    lines = [
        f"# 盘前决策流水线影子周报({window['from']} ~ {window['to']})",
        "",
        f"> 生成时间:{report['generated_at']}|影子运行复盘,仅供校准参考,不构成投资建议。",
        "",
    ]

    # 一、板块预测命中率
    sector = report["sector_hit_rate"]
    summary = sector.get("summary") or {}
    lines.append("## 一、板块预测命中率(预测Top3 vs 当日实际涨幅Top3)")
    lines.append("")
    lines.append(
        f"- 样本:评估 {summary.get('days_evaluated', 0)} 天 / "
        f"{summary.get('picks_total', 0)} 个预测板块;"
        f"命中 {summary.get('picks_hit', 0)} 个板块(命中率 {_fmt_rate(summary.get('hit_rate'))});"
        f"命中 {summary.get('hit_days', 0)} 天(天粒度 {_fmt_rate(summary.get('day_hit_rate'))})"
    )
    missing = summary.get("days_missing_snapshot", 0) + summary.get("days_no_bullish", 0)
    if missing:
        lines.append(f"- 未评估 {missing} 天(缺快照或无看多预测),已计入逐日明细")
    lines.append("")
    evaluated_days = [d for d in sector.get("days") or [] if d.get("evaluated")]
    if evaluated_days:
        lines.append("| 日期 | 预测Top3 | 实际Top3 | 当日命中 | 累计命中率 |")
        lines.append("|---|---|---|---|---|")
        picks_hit = picks_total = 0
        for day in evaluated_days:
            picks_total += len(day["picks"])
            picks_hit += sum(1 for p in day["picks"] if p.get("hit"))
            cumulative = _fmt_rate(picks_hit / picks_total) if picks_total else "样本不足"
            predicted = " ".join(_fmt_board(p) for p in day["picks"])
            actual = ", ".join(
                f"{b.get('board_name') or b.get('board_code')}({b.get('change_pct'):+.2f}%)"
                if isinstance(b.get("change_pct"), (int, float))
                else str(b.get("board_name") or b.get("board_code"))
                for b in day["actual_top"]
            )
            lines.append(
                f"| {day['snapshot_date']} | {predicted} | {actual} | {'✅' if day['hit'] else '❌'} | {cumulative} |"
            )
    else:
        lines.append("- 区间内无可评估样本(缺预测或缺快照),趋势表略")
    lines.append("")

    # 二、三价信号复盘与基线对比
    three_price = report["three_price"]
    comparison = report["baseline_comparison"]
    lines.append("## 二、三价信号复盘(premarket_pipeline vs premarket_outlook 基线)")
    lines.append("")
    lines.append(
        f"- 建议组数:流水线 {comparison['pipeline_suggestion_count']} / "
        f"基线 {comparison['baseline_suggestion_count']};"
        f"待评估:流水线 {comparison['pipeline_pending']} / 基线 {comparison['baseline_pending']}"
    )
    lines.append("")
    lines.append("| horizon(交易日) | 流水线已评估 | 流水线命中率 | 基线已评估 | 基线命中率 | 差值 |")
    lines.append("|---|---|---|---|---|---|")
    for key, row in (comparison.get("horizons") or {}).items():
        delta = (
            f"{row['delta_hit_rate'] * 100:+.1f}pp"
            if isinstance(row.get("delta_hit_rate"), (int, float))
            else "—"
        )
        lines.append(
            f"| {key} | {row['pipeline_completed']} | {_fmt_rate(row.get('pipeline_hit_rate'))} "
            f"| {row['baseline_completed']} | {_fmt_rate(row.get('baseline_hit_rate'))} | {delta} |"
        )
    if comparison.get("pipeline_insufficient_sample") or comparison.get("baseline_insufficient_sample"):
        lines.append("")
        lines.append("- 注:5 日 horizon 已评估样本 < 20,结论按「样本不足」对待,不作为开启模拟盘的依据")
    lines.append("")
    lines.append("三价 outcome_status 分布(流水线,含 pending):")
    lines.append("")
    for key, bucket in (three_price.get("horizons") or {}).items():
        counts = ", ".join(f"{k}={v}" for k, v in (bucket.get("status_counts") or {}).items()) or "无记录"
        lines.append(
            f"- {key} 日:样本 {bucket.get('total', 0)}({counts}),"
            f"已评估 {bucket.get('evaluated', 0)},命中率 {_fmt_rate(bucket.get('hit_rate'))}"
        )
    lines.append("")

    # 三、数据源可用率
    avail = report["source_availability"]
    lines.append("## 三、数据源可用率(来自分析历史 stages.sources 聚合)")
    lines.append("")
    lines.append(
        f"- 运行记录 {avail['runs_total']} 次,其中含阶段明细 {avail['runs_with_stages']} 次"
    )
    lines.append("")
    if avail["sources"]:
        lines.append("| 数据源 | 涉及阶段 | 出现次数 | ok 次数 | ok 率 | 最近降级级别 |")
        lines.append("|---|---|---|---|---|---|")
        for name, bucket in avail["sources"].items():
            lines.append(
                f"| {name} | {bucket['stages'] or '—'} | {bucket['total']} | {bucket['ok']} "
                f"| {_fmt_rate(bucket['ok_rate'])} | {bucket['last_degrade_level'] if bucket['last_degrade_level'] is not None else '—'} |"
            )
    else:
        lines.append("- 区间内无阶段明细,数据源可用率不可评估")
    lines.append("")
    lines.append("降级语义:0=多源一致 / 1=单源 / 2=维度缺失(降置信度) / 3=通道不可用(缺项声明)。")
    lines.append("")
    lines.append("---")
    lines.append("_校准达标(命中率与样本量满足开启条件)后,由人工决定是否开启模拟盘信号,")
    lines.append("本报告不自动变更任何配置。_")
    return "\n".join(lines)


# ────────────────────────── 组装与入口 ──────────────────────────


def build_report(db, start: str, end: str, top_n: int = DEFAULT_TOP_N) -> dict:
    """装载 + 聚合,返回结构化周报数据(渲染前的唯一事实,便于单测)。"""
    preds, snaps = load_sector_window(db, start, end)
    outcomes = (
        db.query(AgentPredictionOutcome)
        .filter(
            AgentPredictionOutcome.agent_name == pcfg.AGENT_NAME,
            AgentPredictionOutcome.prediction_date >= start,
            AgentPredictionOutcome.prediction_date <= end,
        )
        .all()
    )
    return {
        "window": {"from": start, "to": end},
        "generated_at": beijing_now().isoformat(timespec="seconds"),
        "sector_hit_rate": evaluate_sector_hit_rate(preds, snaps, top_n=top_n),
        "three_price": aggregate_three_price_status(outcomes, pcfg.PREDICTION_HORIZONS),
        "baseline_comparison": build_baseline_comparison(
            load_outcomes_summary(db, start, end, pcfg.AGENT_NAME),
            load_outcomes_summary(db, start, end, BASELINE_AGENT_NAME),
        ),
        "source_availability": aggregate_source_availability(
            load_pipeline_histories(db, start, end, pcfg.AGENT_NAME)
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="盘前决策流水线影子周报(只读本地库)")
    parser.add_argument("--from", dest="from_", default=None, help="起始日 YYYY-MM-DD(缺省按 --days 回看)")
    parser.add_argument("--to", default=None, help="截止日 YYYY-MM-DD(缺省今天)")
    parser.add_argument("--days", default=DEFAULT_DAYS, type=int, help=f"回看自然天数(缺省 {DEFAULT_DAYS})")
    parser.add_argument("--out", default=None, help="输出 Markdown 文件路径(缺省打印到 stdout)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    start, end = resolve_window(args)

    init_db()
    db = SessionLocal()
    try:
        report = build_report(db, start, end)
    finally:
        db.close()

    markdown = render_markdown(report)
    if args.out:
        out_path = Path(args.out)
        if out_path.parent and not out_path.parent.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        logger.info("周报已写入: %s", out_path)
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
