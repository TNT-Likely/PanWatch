"""盘前决策流水线 报告组装(纯函数:输入 dict/dataclass,输出 Markdown)。

排版约定:
- 枚举/来源/口径一律经 ``labels`` 模块翻译成中文,未知值回退原样(绝不编造);
- 中文语境使用全角标点;价格统一两位小数;金融标准缩写(PE/PMI/LPR 等)保留;
- 同质数据行聚合(估值入场区逐股行合并且缺口合并),海外指数按名称去重。
"""

from __future__ import annotations

import re
from typing import Any

from src.modules.automation import premarket_config as pcfg
from src.modules.automation.premarket_pipeline.labels import (
    zh_caliber,
    zh_degrade,
    zh_direction,
    zh_level,
    zh_period,
    zh_policy_direction,
    zh_sector_source,
    zh_source,
)
from src.modules.automation.premarket_pipeline.types import (
    MacroCards,
    PickCandidate,
    SectorForecast,
)

_FIELD_LABEL = {"review": "简评", "action": "操作建议"}

#: 模型自由文本的内部词汇定点清洗(枚举/降级记号/半角标点);不做开放式翻译
_CJK_LO, _CJK_HI = "\u4e00", "\u9fff"
_FREE_TEXT_RULES: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"\(degrade=(\d)\)"), lambda m: f"（{zh_degrade(m.group(1))}）"),
    (re.compile(r"degrade=(\d)"), lambda m: zh_degrade(m.group(1))),
    (re.compile(r"(?<![A-Za-z])HIGH(?![A-Za-z])", re.IGNORECASE), "高"),
    (re.compile(r"(?<![A-Za-z])MED(?:IUM)?(?![A-Za-z])", re.IGNORECASE), "中"),
    (re.compile(r"(?<![A-Za-z])LOW(?![A-Za-z])", re.IGNORECASE), "低"),
    (re.compile(r"\bT-1\b"), "前一交易日"),
    (re.compile(r"\bN/A\b"), "—"),
]

#: 中文邻接的半角标点 → 全角(任一侧为汉字即转换;纯数字/代码括号不动)
_HALF_PUNCT = {",": "，", ";": "；", "(": "（", ")": "）"}


def _is_cjk(ch: str) -> bool:
    return bool(ch) and _CJK_LO <= ch <= _CJK_HI


def _sweep_punct(text: str) -> str:
    chars = list(text)
    n = len(chars)
    for i, ch in enumerate(chars):
        target = _HALF_PUNCT.get(ch)
        if not target:
            continue
        prev_ok = i > 0 and _is_cjk(chars[i - 1])
        next_ok = i + 1 < n and _is_cjk(chars[i + 1])
        if prev_ok or next_ok:
            chars[i] = target
    return "".join(chars)


def _zh_free(text: str) -> str:
    """模型/自由文本净化:内部记号替换 + 中文邻接半角标点转全角。"""
    out = str(text or "")
    for pattern, repl in _FREE_TEXT_RULES:
        out = pattern.sub(repl, out)
    return _sweep_punct(out)


def _fmt(v: Any, suffix: str = "") -> str:
    return f"{v}{suffix}" if v is not None else "—"


def _fmt_px(v: Any) -> str:
    """价格统一两位小数;非数值回退 —。"""
    if isinstance(v, (int, float)):
        return f"{v:.2f}"
    return "—"


def _fmt_pct(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:+.2f}%"


def _fmt_conf10(v: Any) -> str:
    """置信度统一 x/10 刻度(0~1 输入自动放大)。"""
    if not isinstance(v, (int, float)):
        return "—"
    scaled = v * 10 if 0 < v <= 1 else v
    return f"{scaled:.0f}/10"


def _fmt_asof(v: Any) -> str:
    """``2026-09-24T10:09:17+08:00`` → ``09-24 10:09``;解析失败回退原值。"""
    text = str(v or "")
    try:
        day, clock = text.split("T", 1)
        return f"{day[5:]} {clock[:5]}"
    except (ValueError, IndexError):
        return text


def _macro_section(cards: MacroCards) -> list[str]:
    lines: list[str] = []
    if cards.status == "stopped":
        lines.append("## 宏观三卡：已硬停（STOP）")
        lines.append(f"\n> {cards.gate_reason}")
        return lines
    if cards.status == "snapshot_only":
        lines.append("## 宏观三卡：仅数据快照")
        lines.append(f"\n> {cards.gate_reason}")
        return lines
    if not cards.cards:
        lines.append("## 宏观三卡：缺失")
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
            f"### {card.get('title') or '未命名卡'}｜方向：{zh_direction(card.get('direction'))}"
            f"｜置信度：{_fmt_conf10(conf)}{note}{cap}"
        )
        if card.get("rationale"):
            lines.append(f"- 依据：{_zh_free(card['rationale'])}")
        policy = card.get("position_policy") or {}
        for key in ("review", "action"):
            if policy.get(key):
                lines.append(f"- {_FIELD_LABEL.get(key, key)}：{_zh_free(policy[key])}")
        diff = policy.get("diff") or []
        if diff:
            items = [
                f"{d.get('item')}（{zh_policy_direction(d.get('direction'))}：{d.get('from')}→{d.get('to')}）"
                for d in diff
                if isinstance(d, dict)
            ]
            lines.append(f"- 立场调整：{'；'.join(str(i) for i in items)}")
        for ann in card.get("annotations") or []:
            lines.append(f"- 标注：{ann}")
    return lines


def _sector_section(forecast: SectorForecast) -> list[str]:
    lines = ["## 板块预判"]
    if not forecast.items:
        lines.append("\n> 板块初筛无结果（快照库为空且实时拉取失败）。")
        return lines
    lines.append("")
    lines.append("| 板块 | 方向 | 置信度 | 阶段 | 动量 | 背离 | 热度 | 来源 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for it in forecast.items:
        lines.append(
            f"| {it.board_name}（{it.board_code}） | {zh_direction(it.direction)}"
            f" | {_fmt_conf10(it.confidence)} | {it.stage or '数据不足'}"
            f" | {_fmt_pct(it.momentum_score)} | {it.divergence or '无'}"
            f" | {'达标' if it.heat else '未达标'} | {zh_sector_source(it.source)} |"
        )
    lines.append("")
    lines.append("> 标注口径：【已观察】＝快照/动量等实测数据；【预测】＝模型推断，次日验证。")
    if not forecast.llm_ok:
        lines.append("> ⚠️ 模型修正不可用，以上为纯算法初筛结果。")
    return lines


def _verify_mark(c: PickCandidate) -> str:
    if c.verified:
        return "已过"
    if c.verification:
        return "未过（转观望）"
    return "未核验"


def _action_label(c: PickCandidate) -> str:
    """最终动作:核验通过即已落信号,与 strategy_signal_runs 保持一致。"""
    if c.verified:
        return "已出信号·关注买入区"
    return c.action_label or "观望"


def _pick_section(candidates: list[PickCandidate]) -> list[str]:
    lines = ["## 候选三价"]
    passed = [c for c in candidates if c.v11_pass]
    if not passed:
        lines.append("\n> 今日无通过硬过滤与 V11 校验的候选。")
        for c in candidates:
            if c.filters_dropped or c.veto_reason:
                lines.append(
                    f"- {c.stock_name}（{c.symbol}）：未入选 —— {c.veto_reason or c.filters_dropped}"
                )
        return lines
    lines.append("")
    lines.append("| 股票 | 板块 | 入场区 | 止损 | 止盈 | 盈亏比 | 龙头分 | 核验 | 动作 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for c in passed:
        lines.append(
            f"| {c.stock_name}（{c.symbol}） | {c.board_name}"
            f" | {_fmt_px(c.entry_low)}～{_fmt_px(c.entry_high)}"
            f" | {_fmt_px(c.stop_loss)} | {_fmt_px(c.target_price)} | {_fmt(c.rr)}"
            f" | {c.leader_score:.2f} | {_verify_mark(c)} | {_action_label(c)} |"
        )
    lines.append("")
    # 逐股明细只展开核验通过的信号股;未过的信息已在表格内,不再逐块罗列
    for c in [x for x in passed if x.verified]:
        tag = "（模型终评）" if c.llm_ok else "（模型终评不可用，采用算法三价）"
        valuation = c.valuation or {}
        lines.append(
            f"### {c.stock_name}（{c.symbol}）{tag}\n"
            f"- 现价：{_fmt_px(c.current_price)}｜评级：{c.grade}档｜估值口径："
            f"{zh_source(valuation.get('source'))}（{zh_degrade(valuation.get('degrade_level'))}）\n"
            f"- 理由：{_zh_free(';'.join(c.reasons)) or '三高动量与估值分位回归（算法）'}\n"
            f"- 风险：{_zh_free(';'.join(c.risks)) or '见证伪信号'}\n"
            f"- 若下列信号出现则本判断可能错：跌破止损 {_fmt_px(c.stop_loss)}；"
            f"板块方向反转；核验项转为未通过。"
        )
        for m in c.missing_dims:
            lines.append(f"- 数据缺口：{m}")
    return lines


def _events_section(events_future: list[dict]) -> list[str]:
    lines = ["## 事件对表（未来 5 个交易日）"]
    if not events_future:
        lines.append("\n> 事件日历未来窗口为空。")
        return lines
    lines.append("")
    for ev in events_future:
        lines.append(
            f"- {ev.get('event_date')}【{zh_level(ev.get('level'))}】{ev.get('name')}"
            f"（{ev.get('scope') or '全球'}）方向：{zh_direction(ev.get('direction'))}"
        )
    return lines


def _global_section(global_rows: list[dict], us_rows: list[dict]) -> list[str]:
    lines = ["## 海外参照与传导映射"]
    us_normalized = [
        {"name": u.get("name"), "price": u.get("current"), "change_pct": u.get("change_pct"),
         "source_tag": "index_quotes"}
        for u in (us_rows or [])
    ]
    # 按指数名去重:实时通道(全球指数)优先,美股通道仅补缺
    seen: set[str] = set()
    rows: list[dict] = []
    for g in list(global_rows or []) + us_normalized:
        name = str(g.get("name") or g.get("symbol") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(g)
    if not rows:
        lines.append("\n> 海外通道不可用，无参照数据（不编造）。")
        return lines
    lines.append("")
    lines.append("| 指数 | 最新 | 涨跌 | 口径 |")
    lines.append("| --- | --- | --- | --- |")
    for g in rows:
        caliber = "实时" if str(g.get("source_tag") or "").endswith("global") else "前一交易日收盘"
        lines.append(
            f"| {g.get('name') or g.get('symbol')} | {_fmt(g.get('price'))} |"
            f" {_fmt_pct(g.get('change_pct'))} | {caliber} |"
        )
    lines.append("")
    lines.append(
        "- 传导路径：利率敏感（高市盈率成长）看美债/美元；出口链看美股需求；"
        "商品链看油/铜；A/H 联动看恒生系。上述任一指数异动超过 1% 时，优先检查对应持仓敞口。"
    )
    return lines


def _declarations_section(declarations: list[dict]) -> list[str]:
    lines = ["## 数据声明（逐项来源与降级）"]
    if not declarations:
        lines.append("\n> 无。")
        return lines

    # 同质行聚合:「估值入场区 ×xx」逐股行合并为一行,缺口同理合并
    grouped: dict[tuple, dict] = {}
    singles: list[dict] = []
    for d in declarations:
        item = str(d.get("item") or "")
        if item.startswith("估值入场区"):
            key = (d.get("source"), d.get("caliber"), d.get("degrade_level"), bool(d.get("note")))
            grouped.setdefault(key, {"count": 0, "note": d.get("note") or ""})
            grouped[key]["count"] += 1
        else:
            singles.append(d)
    for (source, caliber, degrade, _has_note), agg in grouped.items():
        singles.append({
            "item": f"估值入场区（{agg['count']} 只）",
            "source": source,
            "as_of": next(
                (d.get("as_of") for d in declarations
                 if str(d.get("item") or "").startswith("估值入场区")),
                None,
            ),
            "caliber": caliber,
            "degrade_level": degrade,
            "note": agg["note"],
        })

    lines.append("")
    lines.append("| 数据项 | 来源 | 数据时点 | 口径 | 降级 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for d in singles:
        lines.append(
            f"| {d.get('item') or ''} | {zh_source(d.get('source'))} | {_fmt_asof(d.get('as_of'))}"
            f" | {zh_caliber(d.get('caliber'))} | {zh_degrade(d.get('degrade_level'))} |"
        )
    gaps = [d for d in singles if d.get("note")]
    if gaps:
        lines.append("")
        lines.append("**缺口声明**")
        for d in gaps:
            note = str(d["note"])
            if "PE 百分位不可得" in note:
                note = "市盈率百分位不可得，降级为技术面支撑"
            lines.append(f"- {d.get('item')}：{note}")
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
    parts = [f"# 盘前决策流水线报告（{snapshot_date}）"]
    parts.extend(_macro_section(cards))
    parts.extend(_sector_section(forecast))
    parts.extend(_pick_section(candidates))
    parts.extend(_events_section(events_future))
    parts.extend(_global_section(global_rows, us_rows))
    parts.extend(_declarations_section(declarations))
    if stage_errors:
        parts.append("## 阶段错误（降级不中断）")
        for err in stage_errors:
            parts.append(f"- {err.get('stage')}：{err.get('error')}")
    parts.append(
        "\n---\n_本报告由盘前决策流水线自动生成：宏观三卡 → 板块预测 → 选股三价 → "
        "财报核验 → 落库推送。仅供研究参考，不构成投资建议。_"
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
    """通知体（精简）：一行结论 + 候选三价表。"""
    if cards_stopped:
        return (
            f"## 盘前决策流水线 {snapshot_date}\n\n"
            "⛔ 事件日历窗口为空，已硬停。请在「事件日历」导入数据后重试。"
        )
    passed = [c for c in candidates if c.v11_pass]
    lines = [f"## 盘前决策流水线 {snapshot_date}"]
    bull = [it.board_name for it in forecast.items if it.direction == "bullish"][:3]
    if bull:
        lines.append(f"看多板块：{'、'.join(bull)}")
    if not passed:
        lines.append("\n今日无达标候选。")
        return "\n".join(lines)
    lines.append("")
    for c in passed:
        verify_mark = "已过" if c.verified else "⚠️ 未过核验（仅观望）"
        action = _action_label(c)
        lines.append(
            f"- **{c.stock_name}（{c.symbol}）** {action} {verify_mark}｜"
            f"入场 {_fmt_px(c.entry_low)}～{_fmt_px(c.entry_high)}，"
            f"止损 {_fmt_px(c.stop_loss)}，止盈 {_fmt_px(c.target_price)}"
            f"（盈亏比 {_fmt(c.rr)}）"
        )
    lines.append("\n_三价与核验明细见完整报告。_")
    return "\n".join(lines)
