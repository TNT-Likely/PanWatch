"""Build trusted assistant result metadata from durable tool evidence."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from .result_schemas import (
    AssistantEvidence,
    AssistantFact,
    AssistantNextAction,
    AssistantResult,
)
from .tool_descriptors import PANWATCH_TOOL_DESCRIPTORS

logger = logging.getLogger(__name__)

_DESCRIPTORS = {item.tool_name: item for item in PANWATCH_TOOL_DESCRIPTORS}
_MARKDOWN_PREFIX = re.compile(r"^\s{0,3}(?:#{1,6}|[-*>])\s*")


class _ComposedSections(BaseModel):
    summary: str = ""
    inferences: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        return _aware(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        pass
    if re.fullmatch(r"\d{8}", raw):
        try:
            return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    quarter = re.fullmatch(r"(\d{4})[- ]?Q([1-4])", raw, re.IGNORECASE)
    if quarter:
        month = int(quarter.group(2)) * 3
        day = 31 if month in {3, 12} else 30
        return datetime(int(quarter.group(1)), month, day, tzinfo=timezone.utc)
    return None


def _freshness(tool_name: str, data_time: datetime | None, *, date_only: bool = False) -> str:
    data_time = _aware(data_time)
    if data_time is None:
        return "unknown"
    descriptor = _DESCRIPTORS.get(tool_name)
    policy = str(getattr(descriptor, "data_freshness", "") or "")
    age_seconds = max(0.0, (datetime.now(timezone.utc) - data_time).total_seconds())
    age_days = max(0, (datetime.now(timezone.utc).date() - data_time.date()).days)
    if policy == "static":
        return "fresh"
    if tool_name == "get_stock_fundamentals":
        if age_days <= 120:
            return "fresh"
        if age_days <= 200:
            return "delayed"
        return "stale"
    if tool_name == "get_stock_news":
        if age_seconds <= 24 * 60 * 60:
            return "fresh"
        if age_seconds <= 7 * 24 * 60 * 60:
            return "delayed"
        return "stale"
    if tool_name == "get_kline_summary" and date_only:
        if age_days <= 4:
            return "fresh"
        if age_days <= 10:
            return "delayed"
        return "stale"
    if tool_name in {"get_capital_flow", "get_dragon_tiger"} and date_only:
        if age_days <= 1:
            return "fresh"
        if age_days <= 4:
            return "delayed"
        return "stale"
    if date_only:
        if age_days <= 1:
            return "fresh"
        if age_days <= 3:
            return "delayed"
        return "stale"
    if policy == "real_time":
        if age_seconds <= 10 * 60:
            return "fresh"
        if age_seconds <= 60 * 60:
            return "delayed"
        return "stale"
    if policy == "near_real_time":
        if age_seconds <= 2 * 60 * 60:
            return "fresh"
        if age_seconds <= 48 * 60 * 60:
            return "delayed"
        return "stale"
    return "unknown"


def _first_paragraph(answer: str) -> str:
    for block in re.split(r"\n\s*\n", answer.strip()):
        text = " ".join(
            _MARKDOWN_PREFIX.sub("", line).strip()
            for line in block.splitlines()
            if line.strip()
        ).strip()
        if text:
            return text[:500]
    return ""


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("assistant result composer did not return an object")
    return value


def _evidence_and_facts(
    invocations: list[Any],
    language: str,
) -> tuple[list[AssistantEvidence], list[AssistantFact], list[str], list[str]]:
    evidence: list[AssistantEvidence] = []
    facts: list[AssistantFact] = []
    missing_data: list[str] = []
    deterministic_risks: list[str] = []
    quote_points: dict[str, list[tuple[float, datetime | None]]] = {}
    kline_points: dict[str, list[tuple[float, str | None]]] = {}
    for invocation in invocations:
        summary = str(invocation.summary or "").strip()[:2_000]
        if invocation.status != "completed":
            if summary:
                missing_data.append(summary)
            continue
        sources = invocation.source_data if isinstance(invocation.source_data, list) else []
        if not sources:
            sources = [{"name": invocation.tool_name}]
        evidence_ids: list[str] = []
        arguments = invocation.arguments if isinstance(invocation.arguments, dict) else {}
        for index, source in enumerate(sources):
            source = source if isinstance(source, dict) else {"name": str(source)}
            evidence_id = f"ev_{invocation.call_id}_{index + 1}"
            evidence_ids.append(evidence_id)
            published_at = source.get("published_at")
            as_of = source.get("as_of")
            observed_at = _aware(invocation.observed_at)
            if published_at:
                data_at = str(published_at)
                freshness_basis = "published_at"
                reference_time = _parse_datetime(published_at)
            elif as_of:
                data_at = str(as_of)
                freshness_basis = "as_of"
                reference_time = _parse_datetime(as_of)
            else:
                data_at = None
                freshness_basis = "observed_at" if observed_at else "unknown"
                reference_time = observed_at
            evidence.append(
                AssistantEvidence(
                    id=evidence_id,
                    tool_name=invocation.tool_name,
                    source_name=str(source.get("name") or invocation.tool_name),
                    source_url=str(source.get("url") or "").strip() or None,
                    summary=summary,
                    observed_at=observed_at,
                    data_at=data_at,
                    period_start=str(source.get("period_start") or "").strip() or None,
                    period_end=str(source.get("period_end") or "").strip() or None,
                    freshness=_freshness(
                        invocation.tool_name,
                        reference_time,
                        date_only=bool(data_at and re.fullmatch(r"\d{4}-\d{2}-\d{2}", data_at)),
                    ),
                    freshness_basis=freshness_basis,
                    symbol=str(arguments.get("symbol") or "").strip().upper() or None,
                    market=str(arguments.get("market") or "").strip().upper() or None,
                )
            )
        raw_result_data = getattr(invocation, "result_data", None)
        data = raw_result_data if isinstance(raw_result_data, dict) else {}
        symbol = str(data.get("symbol") or arguments.get("symbol") or "").strip().upper()
        market = str(data.get("market") or arguments.get("market") or "CN").strip().upper()
        target = f"{market}:{symbol}" if symbol else ""
        tool_name = invocation.tool_name
        fact_text = ""

        if not data:
            fact_text = summary
            if summary:
                missing_data.append(
                    _localized(
                        language,
                        "该历史记录未保存字段级工具结果，只能恢复当时的工具摘要。",
                        "This historical record did not preserve field-level tool data; only the original tool summary can be restored.",
                    )
                )
        elif tool_name == "get_stock_quote":
            price = _number(data.get("current_price"))
            change_pct = _number(data.get("change_pct"))
            if price is None:
                missing_data.append(
                    _localized(language, f"{target or '该标的'} 缺少最新价。", f"The latest price is missing for {target or 'the symbol'}."),
                )
            else:
                fact_text = _localized(
                    language,
                    f"{target} 最新价为 {_display_number(price)}"
                    + (f"，涨跌幅为 {_display_number(change_pct)}%。" if change_pct is not None else "。"),
                    f"{target} last traded at {_display_number(price)}"
                    + (f", with a {_display_number(change_pct)}% change." if change_pct is not None else "."),
                )
                quote_points.setdefault(target, []).append((price, _aware(invocation.observed_at)))
        elif tool_name == "get_kline_summary":
            close = _number(data.get("last_close"))
            as_of = str(data.get("asof") or data.get("date") or "").strip() or None
            trend = str(data.get("trend") or "").strip()
            parts = []
            if close is not None:
                parts.append(_localized(language, f"收盘价 {_display_number(close)}", f"close {_display_number(close)}"))
                kline_points.setdefault(target, []).append((close, as_of))
            if trend:
                parts.append(_localized(language, f"趋势为{trend}", f"trend: {trend}"))
            if parts:
                prefix = f"{target} K 线" if target else _localized(language, "K 线", "Chart")
                fact_text = f"{prefix}（{as_of}）：" + _localized(language, "，".join(parts) + "。", ", ".join(parts) + ".") if as_of else f"{prefix}：" + _localized(language, "，".join(parts) + "。", ", ".join(parts) + ".")
            if close is None:
                missing_data.append(_localized(language, f"{target or '该标的'} K 线缺少最新收盘价。", f"The chart data for {target or 'the symbol'} lacks a latest close."))
            if as_of is None:
                missing_data.append(_localized(language, f"{target or '该标的'} K 线缺少数据截止日期。", f"The chart data for {target or 'the symbol'} lacks an as-of date."))
        elif tool_name == "get_stock_news":
            items = data.get("items") if isinstance(data.get("items"), list) else []
            count = len(items)
            fact_text = _localized(
                language,
                f"{target} 近 7 天检索到 {count} 条相关新闻。",
                f"The search found {count} related news items for {target} in the last 7 days.",
            )
            if count == 0:
                missing_data.append(_localized(language, f"{target} 近 7 天未检索到新闻，事件风险仍需从其他渠道核验。", f"No news was found for {target} in the last 7 days; event risk still needs verification elsewhere."))
        elif tool_name == "get_stock_fundamentals":
            period = str(data.get("report_date") or data.get("report_period") or data.get("reporting_period") or "").strip()
            labels = (("pe_ratio", "PE"), ("pb_ratio", "PB"), ("roe", "ROE"), ("revenue", _localized(language, "营收", "revenue")), ("net_profit", _localized(language, "净利润", "net profit")))
            values = [f"{label} {_display_number(value)}" for key, label in labels if (value := _number(data.get(key))) is not None]
            if period or values:
                prefix = _localized(language, f"{target} 基本面", f"{target} fundamentals")
                period_text = _localized(language, f"报告期 {period}", f"reporting period {period}") if period else ""
                fact_text = f"{prefix}：" + _localized(language, "，".join([item for item in [period_text, *values] if item]) + "。", ", ".join([item for item in [period_text, *values] if item]) + ".")
            if not period:
                missing_data.append(_localized(language, f"{target} 基本面数据缺少报告期。", f"The fundamentals for {target} lack a reporting period."))
        elif tool_name == "get_capital_flow":
            main_net = _number(data.get("main_net_inflow"))
            main_pct = _number(data.get("main_net_inflow_pct"))
            date = str(data.get("date") or data.get("trade_date") or "").strip()
            if main_net is not None:
                fact_text = _localized(
                    language,
                    f"{target} 主力净流入为 {_display_number(main_net)}"
                    + (f"（{_display_number(main_pct)}%）" if main_pct is not None else "")
                    + (f"，数据日期 {date}。" if date else "。"),
                    f"{target} main net inflow was {_display_number(main_net)}"
                    + (f" ({_display_number(main_pct)}%)" if main_pct is not None else "")
                    + (f" on {date}." if date else "."),
                )
            else:
                missing_data.append(_localized(language, f"{target} 资金流数据缺少主力净流入。", f"Capital flow data for {target} lacks main net inflow."))
        elif tool_name == "find_research_candidates":
            count = int(_number(data.get("count")) or 0)
            snapshot_date = str(data.get("snapshot_date") or "").strip()
            fact_text = _localized(
                language,
                f"机会筛选得到 {count} 个研究候选" + (f"，快照日期 {snapshot_date}。" if snapshot_date else "。"),
                f"Opportunity screening returned {count} research candidates" + (f" for snapshot {snapshot_date}." if snapshot_date else "."),
            )
            if not snapshot_date:
                missing_data.append(_localized(language, "机会筛选结果缺少快照日期。", "The opportunity screening result lacks a snapshot date."))
        elif summary:
            fact_text = summary

        if fact_text:
            facts.append(AssistantFact(text=fact_text, evidence_ids=evidence_ids))

    for target, points in quote_points.items():
        if len(points) < 2:
            continue
        prices = [item[0] for item in points]
        low, high = min(prices), max(prices)
        if low > 0 and (high - low) / low >= 0.005:
            deterministic_risks.append(
                _localized(
                    language,
                    f"{target} 的多次行情观测值在 {_display_number(low)}–{_display_number(high)} 之间；它们来自不同观测时点，判断时应使用最新一次。",
                    f"Repeated quote observations for {target} range from {_display_number(low)} to {_display_number(high)}; they were captured at different times, so use the latest observation.",
                )
            )
    for target, quotes in quote_points.items():
        if not quotes or target not in kline_points:
            continue
        quote_price = quotes[-1][0]
        close, as_of = kline_points[target][-1]
        if close > 0 and abs(quote_price - close) / close >= 0.03:
            deterministic_risks.append(
                _localized(
                    language,
                    f"{target} 最新价 {_display_number(quote_price)} 与 K 线收盘价 {_display_number(close)}"
                    + (f"（{as_of}）" if as_of else "")
                    + "差异较大；两者时点不同，不能直接视为同一价格。",
                    f"The latest price for {target} ({_display_number(quote_price)}) differs materially from the chart close ({_display_number(close)})"
                    + (f" on {as_of}" if as_of else "")
                    + "; they refer to different times and should not be treated as the same price.",
                )
            )
    return evidence, facts, missing_data, deterministic_risks


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _display_number(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _localized(language: str, zh: str, en: str) -> str:
    return en if language == "en-US" else zh


def _next_actions(task_id: int, invocations: list[Any], language: str) -> list[AssistantNextAction]:
    completed_tools = {row.tool_name for row in invocations if row.status == "completed"}
    symbol = ""
    market = ""
    for row in reversed(invocations):
        if row.status != "completed":
            continue
        arguments = row.arguments if isinstance(row.arguments, dict) else {}
        if arguments.get("symbol"):
            symbol = str(arguments["symbol"]).strip().upper()
            market = str(arguments.get("market") or "CN").strip().upper()
            break

    actions: list[AssistantNextAction] = []

    def add(kind: str, label: str, payload: dict, *, approval: bool = False) -> None:
        if len(actions) >= 5:
            return
        actions.append(
            AssistantNextAction(
                id=f"action_{task_id}_{len(actions) + 1}",
                kind=kind,
                label=label,
                payload=payload,
                requires_approval=approval,
            )
        )

    if symbol:
        target = f"{market}:{symbol}"
        if "get_kline_summary" not in completed_tools:
            add(
                "follow_up",
                _localized(language, "分析 K 线", "Analyze chart"),
                {
                    "prompt": _localized(
                        language,
                        f"分析 {target} 的近期 K 线走势和关键支撑压力位",
                        f"Analyze the recent chart trend and key support and resistance levels for {target}",
                    )
                },
            )
        if "get_stock_news" not in completed_tools:
            add(
                "follow_up",
                _localized(language, "查看近期消息", "Review recent news"),
                {
                    "prompt": _localized(
                        language,
                        f"查询并分析 {target} 最近七天的重要新闻和公告",
                        f"Find and analyze important news and filings for {target} from the last seven days",
                    )
                },
            )
        if {"get_kline_summary", "get_stock_news"} <= completed_tools:
            add(
                "follow_up",
                _localized(language, "解释主要风险", "Explain key risks"),
                {
                    "prompt": _localized(
                        language,
                        f"基于已有研究，解释 {target} 当前最重要的风险、失效条件和需要继续验证的数据",
                        f"Based on the existing research, explain the key risks, invalidation conditions, and data that still needs verification for {target}",
                    )
                },
            )
        add(
            "navigate",
            _localized(language, "打开 K 线", "Open chart"),
            {"path": f"/portfolio?{urlencode({'view': 'kline', 'symbol': symbol, 'market': market})}"},
        )
        add(
            "follow_up",
            _localized(language, "横向比较", "Compare peers"),
            {
                "prompt": _localized(
                    language,
                    f"将 {target} 与所属板块和主要指数进行对比",
                    f"Compare {target} with its sector and major market indexes",
                )
            },
        )
        add(
            "tool_proposal",
            _localized(language, "设置价格提醒", "Create price alert"),
            {
                "tool_name": "create_price_alert",
                "arguments": {"symbol": symbol, "market": market},
                "required_inputs": ["direction", "target_price"],
            },
            approval=True,
        )
    elif "get_portfolio" in completed_tools:
        add(
            "navigate",
            _localized(language, "查看持仓", "Open portfolio"),
            {"path": "/portfolio"},
        )
        add(
            "follow_up",
            _localized(language, "继续诊断风险", "Continue risk review"),
            {
                "prompt": _localized(
                    language,
                    "继续分析持仓集中度、相关性和最大回撤风险",
                    "Continue analyzing portfolio concentration, correlation, and drawdown risk",
                )
            },
        )
    elif "find_research_candidates" in completed_tools:
        add(
            "navigate",
            _localized(language, "查看机会", "Open opportunities"),
            {"path": "/opportunities"},
        )

    if "create_price_alert" in completed_tools or "get_price_alerts" in completed_tools:
        actions = [item for item in actions if item.kind != "tool_proposal"]
        add("navigate", _localized(language, "查看提醒", "Open alerts"), {"path": "/alerts"})
    return actions[:5]


async def _compose_sections(
    client: Any,
    *,
    answer: str,
    facts: list[AssistantFact],
    missing_data: list[str],
) -> _ComposedSections | None:
    if client is None or not facts:
        return None
    evidence_payload = [fact.model_dump(mode="json") for fact in facts]
    system_prompt = (
        "You convert an investment assistant answer into a small JSON result. "
        "Use the same language as the answer. Return only a JSON object with keys "
        "summary, inferences, risks, missing_data. The supplied evidence facts are immutable "
        "and must not be rewritten or expanded. Inferences are analytical conclusions. "
        "If supplied facts conflict, describe the conflict in risks instead of silently "
        "choosing one. Do not add sources, prices, dates, or claims that are absent from the input."
    )
    user_content = json.dumps(
        {
            "answer": answer[:12_000],
            "evidence_facts": evidence_payload,
            "known_missing_data": missing_data,
        },
        ensure_ascii=False,
    )
    try:
        raw = await asyncio.wait_for(
            client.chat_multi(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0,
                max_tokens=1_200,
            ),
            timeout=15,
        )
        return _ComposedSections.model_validate(_parse_json_object(raw))
    except Exception:
        logger.warning(
            "Assistant result composition failed; using deterministic result",
            exc_info=True,
        )
        return None


async def build_assistant_result(
    *,
    task_id: int,
    answer: str,
    invocations: list[Any],
    language: str,
    client: Any = None,
) -> AssistantResult:
    """Combine trusted evidence with model-organized, validation-bounded sections."""
    deterministic = build_deterministic_assistant_result(
        task_id=task_id,
        answer=answer,
        invocations=invocations,
        language=language,
    )
    deterministic_facts = deterministic.facts
    missing_data = deterministic.missing_data
    evidence = deterministic.evidence
    composed = await _compose_sections(
        client,
        answer=answer,
        facts=deterministic_facts,
        missing_data=missing_data,
    )
    combined_missing_data = list(missing_data)
    if composed is not None:
        combined_missing_data.extend(composed.missing_data)
    return AssistantResult(
        summary=(
            composed.summary.strip()[:1_000]
            if composed and composed.summary.strip()
            else deterministic.summary
        ),
        facts=deterministic_facts,
        inferences=_clean_unique(list(composed.inferences if composed else []), limit=6),
        risks=_clean_unique(
            [*deterministic.risks, *(composed.risks if composed else [])],
            limit=6,
        ),
        missing_data=_clean_unique(combined_missing_data, limit=6),
        evidence=evidence,
        next_actions=deterministic.next_actions,
    )


def build_deterministic_assistant_result(
    *,
    task_id: int,
    answer: str,
    invocations: list[Any],
    language: str,
) -> AssistantResult:
    """Build a stable result without an extra model call, including legacy tasks."""
    evidence, deterministic_facts, missing_data, deterministic_risks = _evidence_and_facts(
        invocations,
        language,
    )
    evidence = evidence[:20]
    retained_ids = {item.id for item in evidence}
    deterministic_facts = [
        AssistantFact(
            text=fact.text,
            evidence_ids=[item for item in fact.evidence_ids if item in retained_ids],
        )
        for fact in deterministic_facts
        if any(item in retained_ids for item in fact.evidence_ids)
    ]
    risks = list(deterministic_risks)
    if any(item.freshness in {"stale", "unknown"} for item in evidence):
        warning = _localized(
            language,
            "部分依据缺少可验证的数据时点或已经过期，请结合最新数据复核。",
            "Some evidence has an unknown or stale data time; verify it against current data.",
        )
        if warning not in risks:
            risks.append(warning)
    return AssistantResult(
        summary=_first_paragraph(answer),
        facts=deterministic_facts[:8],
        inferences=[],
        risks=_clean_unique(risks, limit=6),
        missing_data=_clean_unique(missing_data, limit=6),
        evidence=evidence,
        next_actions=_next_actions(task_id, invocations, language),
    )


def _clean_unique(items: list[str], *, limit: int) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = item.strip()[:1_000]
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= limit:
            break
    return values
