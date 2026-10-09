"""Read-only request planning and deterministic alert capability checks.

The original user turn is authoritative. Extract only explicit patterns; leave
ambiguous instruments and horizons unresolved instead of guessing intent.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

SUPPORTED_CONDITIONS = ["price", "change_pct", "turnover", "volume", "volume_ratio"]
UNSUPPORTED_CONDITIONS = {
    "bar_close_confirmation": r"收盘(?:后|时|价|确认|站|突破|跌|高于|低于|大于|小于|超过|达到|[><≥≤=])|(?:daily|weekly|bar|candle)\s*close|closing\s+price|at\s+(?:the\s+)?close",
    "moving_average_trigger": r"均线|\b(?:MA|EMA|SMA)\s*\d+|moving\s+average",
    "consecutive_sessions": r"连续\s*[一二两三四五六七八九十\d]+\s*个?\s*(?:交易日|交易周|天|日|周|次)|consecutive|连续满足",
    "stateful_crossing": r"上穿|下穿|首次突破|cross(?:es|ing)?\s*(?:above|below)",
    "event_trigger": r"(?:公告|新闻|财报|业绩)(?:发布|出来|出现|变化|时|后)|when.*(?:news|filing|earnings).*?(?:released|published)",
}
UNSUPPORTED_LABELS = {
    "bar_close_confirmation": ("收盘确认", "bar-close confirmation"),
    "moving_average_trigger": ("均线触发", "moving-average triggers"),
    "consecutive_sessions": ("连续交易日满足", "consecutive sessions"),
    "stateful_crossing": ("有状态穿越", "stateful crossing"),
    "event_trigger": ("事件发布触发", "event-release triggers"),
    "nested_condition_logic": ("混合 AND/OR 条件", "mixed AND/OR conditions"),
}
CONDITION_LABELS = {
    "price": ("价格", "Price"), "change_pct": ("涨跌幅(%)", "Change (%)"),
    "turnover": ("成交额(来源单位)", "Turnover (source units)"),
    "volume": ("成交量(来源单位)", "Volume (source units)"),
    "volume_ratio": ("量比", "Volume ratio"),
}


def original_user_text(request) -> str:
    return next((m.content for m in reversed(request.messages) if m.role == "user" and m.content.strip()), "")


def is_watch_retry(text: str) -> bool:
    """Recognize only a whole-turn retry, never a request with new conditions."""
    return bool(re.fullmatch(
        r"(?:请|帮我)?\s*(?:再试(?:一下|一次)?|再检查(?:一下|一次)?|重新(?:试(?:一下|一次)?|检查(?:一下|一次)?)|重试|try\s+again|retry)[。.!！?？\s]*",
        text.strip(), re.I,
    ))


def resolve_watch_request(text: str, previous_messages, *, context: dict | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Resolve explicit retries from user history before model compression.

    Stop at the nearest substantive user turn. Assistant text, tool content and
    earlier topics cannot supply a rule or authorize a substitute.
    """
    now = now or datetime.now(UTC)
    if not is_watch_retry(text):
        return inspect_watch_request(text, now=now, context=context)
    source = next((m for m in reversed(previous_messages) if m.role == "user" and m.content.strip() and not is_watch_retry(m.content)), None)
    if source and re.search(r"提醒|预警|关注|筛选|盯|\balert\b|\bwatch\b", source.content, re.I):
        anchor = getattr(source, "created_at", None) or now
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)
        plan = inspect_watch_request(source.content, now=anchor, context=context)
        plan.update({
            "evaluated_at": now.isoformat(), "current_turn_text": text,
            "request_source": "previous_user_turn",
            "referenced_user_message_id": getattr(source, "id", None),
        })
        return plan
    plan = inspect_watch_request(text, now=now, context=context)
    plan["requires_clarification"] = ["referenced_request_missing"]
    return plan


def inspect_watch_request(text: str, *, now: datetime | None = None, context: dict | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    context = context or {}
    unsupported = [key for key, pattern in UNSUPPORTED_CONDITIONS.items() if re.search(pattern, text, re.I)]
    scope = "portfolio" if re.search(r"持仓|组合(?!\s*(?:条件|提醒|规则))|portfolio|holdings", text, re.I) else "watchlist" if re.search(r"自选|watchlist", text, re.I) else "instruments"
    instruments = [{"market": m.upper(), "symbol": s.upper().zfill(5) if m.upper() == "HK" and s.isdigit() else s.upper()} for m, s in re.findall(r"\b(CN|HK|US)\s*:\s*([A-Za-z0-9.]+)", text, re.I)]
    if not instruments and context.get("stock_symbol"):
        instruments = [{"symbol": context["stock_symbol"], "market": context.get("stock_market") or "CN"}]
    duration = next((match for match in re.finditer(
        r"(?:未来|持续|有效|关注|盯|接下来)?\s*(\d+|两|二|一|三|四)\s*(周|星期|天|日)(?!均线|移动平均)|(?:for|next|valid for)\s+(\d+|two|one|three)\s*(weeks?|days?)", text, re.I
    ) if not re.search(r"连续\s*$", text[:match.start()])), None)
    horizon = None
    if duration:
        raw = duration.group(1) or duration.group(3)
        count = {"两": 2, "二": 2, "一": 1, "三": 3, "四": 4, "two": 2, "one": 1, "three": 3}.get(raw.lower())
        count = count if count is not None else int(raw)
        unit = duration.group(2) or duration.group(4)
        days = count * (7 if unit in ("周", "星期") or unit.lower().startswith("week") else 1)
        horizon = {"days": days, "expire_at": (now + timedelta(days=days)).isoformat()}
    requested_types = []
    for kind, pattern in {"price": r"价格|股价|突破\s*\d|price", "volume_ratio": r"量比|volume\s+ratio", "change_pct": r"涨跌幅|涨幅|跌幅|change\s*%", "turnover": r"成交额|turnover", "volume": r"成交量|(?<!ratio )\bvolume\b"}.items():
        if re.search(pattern, text, re.I):
            requested_types.append(kind)
    if "volume_ratio" in requested_types and not re.search(r"成交量|\bvolume\s*(?:>=|>|<=|<|=|is)" , text, re.I):
        requested_types = [kind for kind in requested_types if kind != "volume"]
    # Connectors between condition mentions describe rule logic. Connectors in
    # channel choices or explanatory prose must not silently flatten the rule.
    mentions = list(re.finditer(r"价格|股价|量比|涨跌幅|涨幅|跌幅|成交额|成交量|\bprice\b|volume\s+ratio|change\s*%|\bturnover\b|\bvolume\b", text, re.I))
    connectors = set()
    for left, right in zip(mentions, mentions[1:]):
        between = text[left.end():right.start()]
        if re.search(r"且|同时|\band\b", between, re.I):
            connectors.add("and")
        if re.search(r"或|\bor\b", between, re.I):
            connectors.add("or")
    if len(mentions) < 2:
        clause = re.split(r"[，,;；。]|发(?:送|到)?|\bsend\b|\bnotify\b", text, maxsplit=1, flags=re.I)[0]
        if re.search(r"且|同时|\band\b", clause, re.I):
            connectors.add("and")
        if re.search(r"或|\bor\b", clause, re.I):
            connectors.add("or")
    if connectors == {"and", "or"}:
        unsupported.append("nested_condition_logic")
    channels = [name for name, pattern in {"feishu": r"飞书|feishu|lark", "telegram": r"telegram|电报", "email": r"邮件|email"}.items() if re.search(pattern, text, re.I)]
    clarification = []
    if scope == "instruments" and not instruments:
        clarification.append("instrument_lookup_required")
    return {
        "original_text": text, "evaluated_at": now.isoformat(), "instruments": instruments,
        "scope": scope, "horizon": horizon, "requested_condition_types": requested_types,
        "condition_logic": "or" if connectors == {"or"} else "and",
        "requested_channels": channels, "unsupported_conditions": unsupported,
        "requires_clarification": clarification,
        "capabilities": {
            "condition_types": SUPPORTED_CONDITIONS, "condition_logic": ["and", "or"],
            "expiry": True, "notification_channel_selection": True,
            "market_hours": ["trading_only", "always"], "repeat_modes": ["once", "repeat"],
            "semantics": "Stateless quote polling; thresholds are not bar-close or crossing confirmation.",
            "unsupported_conditions": list(UNSUPPORTED_LABELS),
            "continuous_research_task": False,
        },
    }


def condition_summary(group: dict, english: bool = False) -> str:
    parts = []
    for item in group.get("items", []):
        label = CONDITION_LABELS.get(item.get("type"), (item.get("type", "?"), item.get("type", "?")))[int(english)]
        operator = {">=": "≥", "<=": "≤", "!=": "≠"}.get(item.get("op"), item.get("op", "?"))
        parts.append(f"{label} {operator} {item.get('value', '?')}")
    return (" OR " if english else " 或 ").join(parts) if group.get("op") == "or" else (" AND " if english else " 且 ").join(parts)


def alert_capability_error(request, tool_name: str, arguments: dict) -> str | None:
    if tool_name not in ("create_price_alert", "update_price_alert"):
        return None
    # Use the trusted snapshot on resume so relative horizons do not move.
    plan = request.context.setdefault("watch_request", inspect_watch_request(original_user_text(request), context=request.context))
    if "referenced_request_missing" in plan.get("requires_clarification", []):
        return "The retry has no resolved user request. Ask which conditions to retry before proposing an alert."
    changes_conditions = tool_name == "create_price_alert" or any(k in arguments for k in ("condition_group", "direction", "target_price"))
    original = plan.get("original_text") or original_user_text(request)
    # An older pending checkpoint may predate a newly recognized limitation.
    # Recheck capability gaps without moving the saved relative expiry.
    gaps = list(dict.fromkeys([*plan["unsupported_conditions"], *inspect_watch_request(original, context=request.context)["unsupported_conditions"]]))
    if changes_conditions and gaps:
        return "Unsupported alert conditions: " + ", ".join(gaps) + ". Explain the limitation; do not silently substitute intraday thresholds."
    if tool_name == "create_price_alert" and plan["instruments"] and re.search(r"\b(?:CN|HK|US)\s*:\s*[A-Za-z0-9.]+", original, re.I):
        market = str(arguments.get("market") or "CN").upper()
        symbol = str(arguments.get("symbol") or "").upper()
        if market == "HK" and symbol.isdigit():
            symbol = symbol.zfill(5)
        if {"market": market, "symbol": symbol} not in plan["instruments"]:
            return "The proposed alert instrument differs from the checked request. Preserve both symbol and market."
    group = arguments.get("condition_group") or {"op": "and", "items": [{"type": "price"}]}
    supplied = {i.get("type") for i in group.get("items", []) if isinstance(i, dict)}
    missing = set(plan["requested_condition_types"]) - supplied
    if changes_conditions and missing:
        return "The proposed alert omits requested conditions: " + ", ".join(sorted(missing))
    if changes_conditions and len(supplied) > 1 and group.get("op", "and") != plan["condition_logic"]:
        return "The proposed condition logic differs from the user's AND/OR request."
    if tool_name == "create_price_alert" and plan["horizon"]:
        try:
            actual = datetime.fromisoformat(str(arguments.get("expire_at", "")).replace("Z", "+00:00"))
            expected = datetime.fromisoformat(plan["horizon"]["expire_at"])
            if actual.tzinfo is None or abs((actual - expected).total_seconds()) > 600:
                return "expire_at must preserve the checked request horizon and timezone."
        except (TypeError, ValueError):
            return "The requested horizon requires a timezone-qualified expire_at; unlimited validity would omit a user requirement."
    if tool_name == "create_price_alert" and plan["requested_channels"] and not arguments.get("notify_channel_ids"):
        return "Resolve the requested notification channels with get_notification_channels before proposing an alert."
    return None
