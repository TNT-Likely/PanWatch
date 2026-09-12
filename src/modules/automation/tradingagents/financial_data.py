"""A 股财务数据采集 — 用 akshare 拉真实财务报表给 TradingAgents 分析师用。

之前 PanWatch 没采集财报,fundamentals/balance/cashflow/income 工具都返回占位文本,
LLM 没法做真正的基本面分析。本模块用 akshare 的 stock_financial_abstract 拉最近 2 期
真实数据(归母净利润 / 营收 / ROE / 毛利率 / 资产负债率 / 经营现金流等),塞给
对应工具。

只支持 A 股(6 位数字)。失败时返回 None,toolkit_adapter 退回轻量 quote 数据。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def fetch_financial_abstract(symbol: str) -> dict | None:
    """拉一只 A 股的财务摘要,返回结构化字典。

    Returns:
        {
            "periods": ["20260331", "20251231", ...],   # 最近 N 期
            "indicators": {
                "归母净利润": {"20260331": -6.56e8, "20251231": -8.78e9, ...},
                ...
            },
            "categories": {
                "盈利能力": {"毛利率": {...}, "净资产收益率(ROE)": {...}},
                "成长能力": {...},
                ...
            },
        }
        或 None(akshare 失败 / 非 A 股 / 数据为空)
    """
    if not (symbol and len(symbol) == 6 and symbol.isdigit()):
        return None
    try:
        import akshare as ak
    except ImportError:
        logger.warning("[TA fin] akshare 未安装,无法拉财报")
        return None

    try:
        df = ak.stock_financial_abstract(symbol=symbol)
    except Exception as e:
        logger.warning(f"[TA fin] stock_financial_abstract({symbol}) 失败: {e}")
        return None
    if df is None or df.empty:
        return None

    # 列结构:[选项, 指标, 20260331, 20251231, ...] —— 取最近 N 期
    period_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    if not period_cols:
        return None
    recent_periods = period_cols[:6]  # 最多 6 期(1.5 年)

    indicators: dict[str, dict[str, float | None]] = {}
    categories: dict[str, dict[str, dict[str, float | None]]] = {}

    for _, row in df.iterrows():
        cat = str(row.get("选项") or "").strip()
        name = str(row.get("指标") or "").strip()
        if not name:
            continue
        values: dict[str, float | None] = {}
        for p in recent_periods:
            v = row.get(p)
            try:
                values[p] = float(v) if v is not None and str(v) not in ("nan", "NaN") else None
            except (TypeError, ValueError):
                values[p] = None
        indicators[name] = values
        if cat:
            categories.setdefault(cat, {})[name] = values

    return {
        "periods": recent_periods,
        "indicators": indicators,
        "categories": categories,
    }


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "N/A"
    av = abs(v)
    if av >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if av >= 1e4:
        return f"{v / 1e4:.2f} 万"
    return f"{v:.2f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:.2f}%"


def _fmt_period(p: str) -> str:
    """20260331 → 2026Q1, 20251231 → 2025Q4(年报)"""
    if len(p) != 8:
        return p
    y, m, d = p[:4], p[4:6], p[6:8]
    q = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}.get(m, m)
    return f"{y}{q}"


def render_fundamentals_summary(data: dict) -> str:
    """渲染基本面综合摘要(给 get_fundamentals 用)。"""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No financial data available]"

    lines = ["[Real Financial Data from PanWatch (akshare)]"]
    lines.append(f"Reporting periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")

    key_metrics = [
        ("营业总收入", _fmt_num),
        ("归母净利润", _fmt_num),
        ("扣非净利润", _fmt_num),
        ("基本每股收益", lambda v: f"{v:.2f} 元" if v is not None else "N/A"),
        ("毛利率", _fmt_pct),
        ("净资产收益率(ROE)", _fmt_pct),
        ("资产负债率", _fmt_pct),
        ("经营现金流量净额", _fmt_num),
    ]
    for name, fmt in key_metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")

    lines.append("")
    lines.append(
        "Note: This is REAL data pulled from official A-share financial reports. "
        "Use these numbers to ground your fundamental analysis (revenue trends, "
        "profitability, leverage). Do NOT invent additional numbers."
    )
    return "\n".join(lines)


def render_income_statement(data: dict) -> str:
    """渲染利润表(给 get_income_statement 用)。"""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No income statement data]"
    lines = ["[Income Statement (real data from PanWatch / akshare)]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("营业总收入", _fmt_num),
        ("营业成本", _fmt_num),
        ("归母净利润", _fmt_num),
        ("净利润", _fmt_num),
        ("扣非净利润", _fmt_num),
        ("毛利率", _fmt_pct),
        ("销售净利率", _fmt_pct),
        ("期间费用率", _fmt_pct),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)


def render_balance_sheet(data: dict) -> str:
    """渲染资产负债表(给 get_balance_sheet 用)。"""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No balance sheet data]"
    lines = ["[Balance Sheet (real data from PanWatch / akshare)]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("股东权益合计(净资产)", _fmt_num),
        ("每股净资产", lambda v: f"{v:.2f} 元" if v is not None else "N/A"),
        ("商誉", _fmt_num),
        ("资产负债率", _fmt_pct),
        ("总资产报酬率(ROA)", _fmt_pct),
        ("净资产收益率(ROE)", _fmt_pct),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)


def render_cashflow(data: dict) -> str:
    """渲染现金流量表(给 get_cashflow 用)。"""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No cash flow data]"
    lines = ["[Cash Flow Statement (real data from PanWatch / akshare)]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("经营现金流量净额", _fmt_num),
        ("每股现金流", lambda v: f"{v:.2f} 元" if v is not None else "N/A"),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)
