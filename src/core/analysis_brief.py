"""从分析历史快速提取 AI 聊天用的产业周期视角 / 深度分析摘要（无报告则跳过）。"""

from __future__ import annotations

from typing import Any

from src.core.lmd_report_snapshot import snapshot_from_history_record


def format_lmd_brief(record: Any | None) -> str | None:
    """产业周期视角结论：优先结构化快照，忽略无报告。"""
    if not record:
        return None
    snap = snapshot_from_history_record(record)
    if not snap.has_report and not snap.has_metrics():
        return None

    parts: list[str] = []
    if snap.valuation_score is not None:
        parts.append(f"估值{snap.valuation_score}分")
    if snap.valuation_verdict:
        parts.append(snap.valuation_verdict)
    if snap.expectation_hint:
        parts.append(f"预期差{snap.expectation_hint}")
    if snap.profit_yoy_pct is not None:
        parts.append(f"净利同比{snap.profit_yoy_pct:+.1f}%")
    if snap.revenue_yoy_pct is not None:
        parts.append(f"营收同比{snap.revenue_yoy_pct:+.1f}%")
    if not parts:
        return None

    date = str(getattr(record, "analysis_date", "") or "").strip()
    prefix = f"产业周期视角({date})" if date else "产业周期视角"
    return f"{prefix}：{'，'.join(parts)}"


def _truncate(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def format_deep_brief(record: Any | None) -> str | None:
    """深度分析结论：从 tradingagents raw_data 提取评级与理由。"""
    if not record:
        return None

    raw = getattr(record, "raw_data", None) or {}
    if not isinstance(raw, dict):
        raw = {}

    sug = raw.get("suggestion") if isinstance(raw.get("suggestion"), dict) else {}
    action_label = str(sug.get("action_label") or "").strip()
    confidence = sug.get("confidence")
    reason = str(sug.get("reason") or sug.get("signal") or "").strip()

    final_text = str(raw.get("final_decision") or "").strip()
    if not final_text:
        final_text = str(raw.get("final_trade_decision") or "").strip()

    parts: list[str] = []
    if action_label:
        parts.append(action_label)
    if isinstance(confidence, (int, float)):
        parts.append(f"置信度 {float(confidence):.1f}/10")
    if reason:
        parts.append(_truncate(reason, 120))
    elif final_text:
        parts.append(_truncate(final_text, 120))
    elif getattr(record, "title", None):
        parts.append(_truncate(str(record.title), 80))

    if not parts:
        content = str(getattr(record, "content", "") or "").strip()
        if not content:
            return None
        parts.append(_truncate(content, 120))

    date = str(getattr(record, "analysis_date", "") or "").strip()
    prefix = f"深度分析({date})" if date else "深度分析"
    return f"{prefix}：{'，'.join(parts)}"


def load_latest_deep_reports_by_symbol(db: Any, symbols: list[str]) -> dict[str, Any]:
    if not symbols:
        return {}

    from src.web.models import AnalysisHistory

    rows = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == "tradingagents",
            AnalysisHistory.stock_symbol.in_(symbols),
        )
        .order_by(
            AnalysisHistory.analysis_date.desc(),
            AnalysisHistory.updated_at.desc(),
            AnalysisHistory.id.desc(),
        )
        .all()
    )
    out: dict[str, Any] = {}
    for row in rows:
        sym = row.stock_symbol
        if sym not in out:
            out[sym] = row
    return out
