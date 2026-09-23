"""盘前决策流水线 报告组装(纯函数:输入 dict/dataclass,输出 Markdown)。"""

from __future__ import annotations

from typing import Any

from src.modules.automation import premarket_config as pcfg
from src.modules.automation.premarket_pipeline.types import (
    MacroCards,
    PickCandidate,
    SectorForecast,
)

_DIRECTION_LABEL = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}


def _fmt(v: Any, suffix: str = "") -> str:
    return f"{v}{suffix}" if v is not None else "N/A"


def _fmt_pct(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "N/A"
    return f"{v:+.2f}%"


def _macro_section(cards: MacroCards) -> list[str]:
    lines: list[str] = []
    if cards.status == "stopped":
        lines.append("## 宏观三卡:已硬停(STOP)")
        lines.append(f"\n> {cards.gate_reason}")
        return lines
    if cards.status == "snapshot_only":
        lines.append("## 宏观三卡:仅数据快照")
        lines.append(f"\n> {cards.gate_reason}")
        return lines
    if not cards.cards:
        lines.append("## 宏观三卡:缺失")
        if cards.gate_reason:
            lines.append(f"\n> {cards.gate_reason}")
        return lines
    lines.append("## 宏观三卡")
    for card in cards.cards:
        conf = card.get("confidence")
        note = ""
        if isinstance(conf, (int, float)) and conf < pcfg.CONFIDENCE_FORMULA["decision_threshold"]:
            note = "（⚠️ 置信度低于阈值，不建议作为决策依据）"
        cap = ""
        if cards.confidence_cap is not None and isinstance(conf, (int, float)) and conf >= cards.confidence_cap:
            cap = f"（通道弱，置信度封顶 {cards.confidence_cap}）"
        lines.append(
            f"### {card.get('title') or '未命名卡'}｜方向:{card.get('direction') or '中性'}"
            f"｜置信度:{_fmt(conf)}/10{note}{cap}"
        )
        if card.get("rationale"):
            lines.append(f"- 依据:{card['rationale']}")
        policy = card.get("position_policy") or {}
        for key in ("review", "action"):
            if policy.get(key):
                lines.append(f"- {key}:{policy[key]}")
        diff = policy.get("diff") or []
        if diff:
            items = [
                f"{d.get('item')}({d.get('direction')}:{d.get('from')}→{d.get('to')})"
                for d in diff
                if isinstance(d, dict)
            ]
            lines.append(f"- 立场调整:{';'.join(str(i) for i in items)}")
        for ann in card.get("annotations") or []:
            lines.append(f"- 标注:{ann}")
    return lines


def _sector_section(forecast: SectorForecast) -> list[str]:
    lines = ["## 板块预判"]
    if not forecast.items:
        lines.append("\n> 板块初筛无结果(快照库为空且实时拉取失败)。")
        return lines
    lines.append("")
    lines.append("| 板块 | 方向 | 置信度 | 阶段 | 动量 | 背离 | 热度 | 来源 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for it in forecast.items:
        lines.append(
            f"| {it.board_name}({it.board_code}) | {_DIRECTION_LABEL.get(it.direction, it.direction)}"
            f" | {it.confidence:.2f} | {it.stage or '未知'}"
            f" | {_fmt(it.momentum_score)} | {it.divergence or '无'}"
            f" | {'达标' if it.heat else '未达标'} | {it.source} |"
        )
    lines.append("")
    lines.append("> 标注口径:【已观察】=快照/动量等实测数据;【预测】=模型推断,明日验证。")
    if not forecast.llm_ok:
        lines.append("> ⚠️ LLM 修正不可用,以上为纯算法初筛结果。")
    return lines


def _pick_section(candidates: list[PickCandidate]) -> list[str]:
    lines = ["## 候选三价"]
    passed = [c for c in candidates if c.v11_pass]
    if not passed:
        lines.append("\n> 今日无通过硬过滤与 V11 校验的候选。")
        for c in candidates:
            if c.filters_dropped or c.veto_reason:
                lines.append(
                    f"- {c.stock_name}({c.symbol}):未入选 —— {c.veto_reason or c.filters_dropped}"
                )
        return lines
    lines.append("")
    lines.append("| 股票 | 板块 | 入场区 | 止损 | 止盈 | 盈亏比 | 龙头分 | 核验 | 动作 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for c in passed:
        verify_mark = "✅" if c.verified else ("⚠️ 未过(转观望)" if c.verification else "未核验")
        lines.append(
            f"| {c.stock_name}({c.symbol}) | {c.board_name}"
            f" | [{_fmt(c.entry_low)}, {_fmt(c.entry_high)}]"
            f" | {_fmt(c.stop_loss)} | {_fmt(c.target_price)} | {_fmt(c.rr)}"
            f" | {c.leader_score:.2f} | {verify_mark} | {c.action_label} |"
        )
    lines.append("")
    for c in passed:
        tag = "（LLM 终评）" if c.llm_ok else "（AI 终评不可用,算法三价）"
        lines.append(
            f"### {c.stock_name}({c.symbol}){tag}\n"
            f"- 现价:{_fmt(c.current_price)}｜评级:{c.grade} 档｜估值口径:"
            f"{(c.valuation or {}).get('source')}(degrade={(c.valuation or {}).get('degrade_level')})\n"
            f"- 理由:{';'.join(c.reasons) or '三高动量与估值分位回归(算法)'}\n"
            f"- 风险:{';'.join(c.risks) or '见证伪信号'}\n"
            f"- 若下列信号出现则本判断可能错:跌破止损 {_fmt(c.stop_loss)};"
            f"板块方向反转;核验项转 FAIL。"
        )
        for m in c.missing_dims:
            lines.append(f"- 数据缺口:{m}")
    return lines


def _events_section(events_future: list[dict]) -> list[str]:
    lines = ["## 事件对表(未来 5 个交易日)"]
    if not events_future:
        lines.append("\n> 事件日历未来窗口为空。")
        return lines
    lines.append("")
    for ev in events_future:
        lines.append(
            f"- [{ev.get('event_date')}][{ev.get('level')}] {ev.get('name')}"
            f"({ev.get('scope') or '全球'}) 方向:{ev.get('direction') or 'neutral'}"
        )
    return lines


def _global_section(global_rows: list[dict], us_rows: list[dict]) -> list[str]:
    lines = ["## 海外参照与传导映射"]
    rows = list(global_rows or []) + [
        {"name": u.get("name"), "price": u.get("current"), "change_pct": u.get("change_pct"),
         "source_tag": "index_quotes"}
        for u in (us_rows or [])
    ]
    if not rows:
        lines.append("\n> 海外通道不可用,无参照数据(不编造)。")
        return lines
    lines.append("")
    lines.append("| 指数 | 最新 | 涨跌 | 口径 |")
    lines.append("| --- | --- | --- | --- |")
    for g in rows:
        caliber = "实时" if str(g.get("source_tag") or "").endswith("global") else "T-1 收盘"
        lines.append(
            f"| {g.get('name') or g.get('symbol')} | {_fmt(g.get('price'))} |"
            f" {_fmt_pct(g.get('change_pct'))} | {caliber} |"
        )
    lines.append("")
    lines.append(
        "- 传导路径:利率敏感(高 PE 成长)看美债/美元;出口链看美股需求;"
        "商品链看油/铜;A/H 联动看恒生系。上述任一指数异动 >1% 时,优先检查对应持仓敞口。"
    )
    return lines


def _declarations_section(declarations: list[dict]) -> list[str]:
    lines = ["## 数据声明(逐项来源与降级)"]
    if not declarations:
        lines.append("\n> 无。")
        return lines
    lines.append("")
    lines.append("| 数据项 | 来源 | as_of | 口径 | 降级级别 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for d in declarations:
        lines.append(
            f"| {d.get('item') or ''} | {d.get('source') or ''} | {d.get('as_of') or ''}"
            f" | {d.get('caliber') or ''} | {d.get('degrade_level', '')} |"
        )
    for d in declarations:
        if d.get("note"):
            lines.append(f"- 缺口:{d['item']}:{d['note']}")
    return lines


def render_report(
    *,
    snapshot_date: str,
    cards: MacroCards,
    forecast: SectorForecast,
    candidates: list[PickCandidate],
    declarations: list[dict],
    events_future: list[dict],
    global_rows: list[dict],
    us_rows: list[dict],
    stage_errors: list[dict] | None = None,
) -> str:
    parts = [f"# 盘前决策流水线报告({snapshot_date})"]
    parts.extend(_macro_section(cards))
    parts.extend(_sector_section(forecast))
    parts.extend(_pick_section(candidates))
    parts.extend(_events_section(events_future))
    parts.extend(_global_section(global_rows, us_rows))
    parts.extend(_declarations_section(declarations))
    if stage_errors:
        parts.append("## 阶段错误(降级不中断)")
        for err in stage_errors:
            parts.append(f"- {err.get('stage')}: {err.get('error')}")
    parts.append(
        "\n---\n_本报告由盘前决策流水线自动生成:宏观三卡 → 板块预测 → 选股三价 → "
        "财报核验 → 落库推送。仅供研究参考,不构成投资建议。_"
    )
    return "\n".join(parts)


def render_notify(
    *,
    snapshot_date: str,
    cards: MacroCards,
    forecast: SectorForecast,
    candidates: list[PickCandidate],
    cards_stopped: bool = False,
) -> str:
    """通知体(精简):一行结论 + 候选三价表。"""
    if cards_stopped:
        return (
            f"## 盘前决策流水线 {snapshot_date}\n\n"
            "⛔ 事件日历窗口为空,已硬停。请在「事件日历」导入数据后重试。"
        )
    passed = [c for c in candidates if c.v11_pass]
    lines = [f"## 盘前决策流水线 {snapshot_date}"]
    bull = [it.board_name for it in forecast.items if it.direction == "bullish"][:3]
    if bull:
        lines.append(f"看多板块:{'、'.join(bull)}")
    if not passed:
        lines.append("\n今日无达标候选。")
        return "\n".join(lines)
    lines.append("")
    for c in passed:
        verify_mark = "✅" if c.verified else "⚠️ 未过核验(仅观望)"
        lines.append(
            f"- **{c.stock_name}({c.symbol})** {c.action_label} {verify_mark}｜"
            f"入场 [{c.entry_low}, {c.entry_high}] 止损 {c.stop_loss} "
            f"止盈 {c.target_price}(盈亏比 {c.rr})"
        )
    lines.append("\n_三价与核验明细见完整报告。_")
    return "\n".join(lines)
