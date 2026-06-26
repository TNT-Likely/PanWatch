"""从老马视角报告 Markdown 提取关注列表可用的估值/基本面快照。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields as dataclass_fields
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.core.hermes_config import local_skill_agent_name, parse_local_skill_slug

LMD_SKILL_SLUG = "lmd-finance-perspective"
LMD_AGENT_NAME = "lmd_outlook"
LMD_SKILL_AGENT_NAME = local_skill_agent_name(LMD_SKILL_SLUG)

REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"

_HEADING_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*#*$")
_PE_TTM_RE = re.compile(
    r"(?:当前)?PE\s*[（(]?TTM[）)]?\s*[：:]?\s*([-\d.]+)\s*倍",
    re.I,
)
_PE_SIMPLE_RE = re.compile(
    r"PE(?:\（TTM\）|\(TTM\)|（TTM）)?\s*([-\d.]+)\s*倍",
    re.I,
)
_PE_NEGATIVE_RE = re.compile(r"PE为负", re.I)
_PB_RE = re.compile(r"PB\s*([-\d.]+)\s*倍", re.I)
_FORWARD_PE_RE = re.compile(r"前瞻PE(?:约|为)?\s*([-\d.]+)\s*倍", re.I)
_VAL_SCORE_RE = re.compile(r"估值\s*(\d+)\s*分")
_PROFIT_YOY_RE = re.compile(
    r"(?:归母)?净利润[^。\n]{0,100}?同比\s*([+-]?\d+(?:\.\d+)?)\s*%",
    re.I,
)
_VAL_VERDICT_RE = re.compile(
    r"估值小结[^：:\n]*[：:]\s*(?:\*\*)?([^*\n—-]+)",
)
_VAL_VERDICT_ALT_RE = re.compile(
    r"我给\*\*估值\d+分[———-]\s*([^。*\n]+)",
)
_EXPECT_HINT_RE = re.compile(
    r"预期差(?:评分|小结)\d*分?[———-]\s*([^。*\n]+)",
)
_ROE_RE = re.compile(
    r"(?:ROE|净资产收益率)[^。\n\d%]{0,40}?([+-]?\d+(?:\.\d+)?)\s*%",
    re.I,
)
_GROSS_MARGIN_RE = re.compile(
    r"毛利率[^。\n]{0,80}?([+-]?\d+(?:\.\d+)?)\s*%",
    re.I,
)
_REVENUE_YOY_RE = re.compile(
    r"营收[^。\n]{0,100}?同比\s*([+-]?\d+(?:\.\d+)?)\s*%",
    re.I,
)
_EPS_RE = re.compile(
    r"EPS(?:约|[：:])?\s*([-\d.]+)\s*元",
    re.I,
)


@dataclass
class LmdReportSnapshot:
    pe_ttm: float | None = None
    forward_pe: float | None = None
    pb: float | None = None
    profit_yoy_pct: float | None = None
    revenue_yoy_pct: float | None = None
    roe_pct: float | None = None
    gross_margin_pct: float | None = None
    consensus_eps: float | None = None
    valuation_score: int | None = None
    valuation_verdict: str | None = None
    expectation_hint: str | None = None
    report_date: str | None = None
    has_report: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def has_metrics(self) -> bool:
        return any(
            v is not None
            for v in (
                self.pe_ttm,
                self.forward_pe,
                self.pb,
                self.profit_yoy_pct,
                self.revenue_yoy_pct,
                self.roe_pct,
                self.gross_margin_pct,
                self.consensus_eps,
                self.valuation_score,
                self.valuation_verdict,
                self.expectation_hint,
            )
        )


def is_lmd_report_agent(agent_name: str | None) -> bool:
    if not agent_name:
        return False
    if agent_name == LMD_AGENT_NAME:
        return True
    return parse_local_skill_slug(agent_name) == LMD_SKILL_SLUG


def _heading_matches_section(title: str, section: str) -> bool:
    t = title.strip()
    if section == "valuation":
        if re.search(r"路径|情景|推演", t):
            return False
        return "估值" in t and "基本面" not in t
    if section == "fundamentals":
        return "基本面" in t
    return False


def _extract_section(markdown: str, section: str) -> str:
    lines = (markdown or "").splitlines()
    in_section = False
    section_level = 0
    chunks: list[str] = []
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2)
            if _heading_matches_section(title, section):
                in_section = True
                section_level = level
                continue
            if in_section and level <= section_level:
                break
        if in_section:
            chunks.append(line)
    return "\n".join(chunks)


def _first_float(pattern: re.Pattern[str], text: str) -> float | None:
    m = pattern.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
    m = pattern.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _first_profit_yoy(text: str) -> float | None:
    m = _PROFIT_YOY_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _first_verdict(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text or "")
    if not m:
        return None
    value = re.sub(r"\*+", "", m.group(1)).strip()
    return value or None


def _truncate(text: str | None, limit: int = 18) -> str | None:
    if not text:
        return None
    compact = re.sub(r"\s+", "", text.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def extract_lmd_report_snapshot(markdown: str, *, report_date: str | None = None) -> LmdReportSnapshot:
    """从老马视角报告正文提取卡片展示用的结构化快照。"""
    content = (markdown or "").strip()
    if not content:
        return LmdReportSnapshot(has_report=False, report_date=report_date)

    valuation = _extract_section(content, "valuation")
    fundamentals = _extract_section(content, "fundamentals")
    search_text = valuation or content

    pe_ttm = _first_float(_PE_TTM_RE, search_text)
    if pe_ttm is None:
        pe_ttm = _first_float(_PE_SIMPLE_RE, search_text)
    if pe_ttm is None and _PE_NEGATIVE_RE.search(search_text):
        pe_ttm = -1.0

    forward_pe = _first_float(_FORWARD_PE_RE, search_text)
    pb = _first_float(_PB_RE, search_text)
    profit_yoy_pct = _first_profit_yoy(fundamentals) or _first_profit_yoy(content)
    revenue_yoy_pct = _first_float(_REVENUE_YOY_RE, fundamentals) or _first_float(
        _REVENUE_YOY_RE, content
    )
    roe_pct = _first_float(_ROE_RE, fundamentals) or _first_float(_ROE_RE, content)
    gross_margin_pct = _first_float(_GROSS_MARGIN_RE, fundamentals) or _first_float(
        _GROSS_MARGIN_RE, content
    )
    consensus_eps = _first_float(_EPS_RE, search_text) or _first_float(_EPS_RE, content)
    valuation_score = _first_int(_VAL_SCORE_RE, search_text)
    valuation_verdict = _truncate(
        _first_verdict(_VAL_VERDICT_RE, search_text)
        or _first_verdict(_VAL_VERDICT_ALT_RE, search_text),
    )
    expectation_hint = _truncate(
        _first_verdict(_EXPECT_HINT_RE, _extract_section(content, "fundamentals"))
        or _first_verdict(_EXPECT_HINT_RE, content),
        limit=20,
    )

    return LmdReportSnapshot(
        pe_ttm=pe_ttm,
        forward_pe=forward_pe,
        pb=pb,
        profit_yoy_pct=profit_yoy_pct,
        revenue_yoy_pct=revenue_yoy_pct,
        roe_pct=roe_pct,
        gross_margin_pct=gross_margin_pct,
        consensus_eps=consensus_eps,
        valuation_score=valuation_score,
        valuation_verdict=valuation_verdict,
        expectation_hint=expectation_hint,
        report_date=report_date,
        has_report=False,
    )


def snapshot_from_dict(
    data: dict[str, Any] | None,
    *,
    report_date: str | None = None,
) -> LmdReportSnapshot:
    """从 raw_data.lmd_snapshot 还原快照对象。"""
    payload = dict(data or {})
    field_names = {f.name for f in dataclass_fields(LmdReportSnapshot)}
    filtered = {k: v for k, v in payload.items() if k in field_names}
    if report_date and not filtered.get("report_date"):
        filtered["report_date"] = report_date
    return LmdReportSnapshot(**filtered)


def attach_lmd_snapshot_to_raw_data(
    raw_data: dict | None,
    content: str,
    *,
    report_date: str | None = None,
) -> dict[str, Any]:
    """报告入库时写入结构化快照，供关注列表直接读取。"""
    payload = dict(raw_data or {})
    snap = extract_lmd_report_snapshot(content, report_date=report_date)
    snap.has_report = bool((content or "").strip())
    payload["lmd_snapshot"] = snap.to_dict()
    return payload


def _should_resolve_lmd_report(record: Any) -> bool:
    if getattr(record, "stock_symbol", "") in ("", "*"):
        return False
    if record.agent_name == LMD_AGENT_NAME:
        return True
    slug = parse_local_skill_slug(record.agent_name)
    if slug == LMD_SKILL_SLUG:
        return True
    from src.core.hermes_runner import is_incomplete_lmd_report

    if slug and is_incomplete_lmd_report(record.content or ""):
        return True
    return False


def _resolve_report_content(record: Any) -> str:
    content = record.content or ""
    if not _should_resolve_lmd_report(record):
        return content

    analysis_date: date | None = None
    try:
        analysis_date = datetime.strptime(
            str(record.analysis_date or ""), "%Y-%m-%d"
        ).date()
    except (ValueError, TypeError):
        analysis_date = None

    from src.core.hermes_runner import resolve_lmd_report_content

    return resolve_lmd_report_content(
        content,
        symbol=record.stock_symbol,
        reports_dir=REPORTS_DIR,
        analysis_date=analysis_date,
    )


def snapshot_from_history_record(record: Any) -> LmdReportSnapshot:
    report_date = str(record.analysis_date or "") or None
    cached = (getattr(record, "raw_data", None) or {}).get("lmd_snapshot")
    if isinstance(cached, dict) and cached:
        snap = snapshot_from_dict(cached, report_date=report_date)
        snap.has_report = True
        return snap

    content = _resolve_report_content(record)
    snap = extract_lmd_report_snapshot(content, report_date=report_date)
    snap.has_report = bool(content.strip())
    return snap


def load_latest_lmd_reports_by_symbol(db: Any, symbols: list[str]) -> dict[str, Any]:
    """按 symbol 取最新一条老马视角报告记录。"""
    if not symbols:
        return {}

    from sqlalchemy import or_
    from src.web.models import AnalysisHistory

    rows = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.stock_symbol.in_(symbols),
            or_(
                AnalysisHistory.agent_name == LMD_AGENT_NAME,
                AnalysisHistory.agent_name == LMD_SKILL_AGENT_NAME,
            ),
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
