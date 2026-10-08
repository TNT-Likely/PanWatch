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
    "bar_close_confirmation": r"收盘(?:后|时|价|确认|站|突破|跌)|(?:daily|weekly|bar|candle)\s*close|at\s+(?:the\s+)?close",
    "moving_average_trigger": r"均线|\b(?:MA|EMA|SMA)\s*\d+|moving\s+average",
    "consecutive_sessions": r"连续\s*[一二两三四五六七八九十\d]+\s*(?:天|日|周|次)|consecutive|连续满足",
    "stateful_crossing": r"上穿|下穿|首次突破|cross(?:es|ing)?\s*(?:above|below)",
    "event_trigger": r"(?:公告|新闻|财报|业绩)(?:发布|出来|出现|变化|时|后)|when.*(?:news|filing|earnings).*?(?:released|published)",
}
CONDITION_LABELS = {
    "price": ("价格", "Price"), "change_pct": ("涨跌幅(%)", "Change (%)"),
    "turnover": ("成交额(来源单位)", "Turnover (source units)"),
    "volume": ("成交量(来源单位)", "Volume (source units)"),
    "volume_ratio": ("量比", "Volume ratio"),
}


def original_user_text(request) -> str:
    return next((m.content for m in reversed(request.messages) if m.role == "user" and m.content.strip()), "")


def inspect_watch_request(text: str, *, now: datetime | None = None, context: dict | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    context = context or {}
    unsupported = [key for key, pattern in UNSUPPORTED_CONDITIONS.items() if re.search(pattern, text, re.I)]
    scope = "portfolio" if re.search(r"持仓|组合|portfolio|holdings", text, re.I) else "watchlist" if re.search(r"自选|watchlist", text, re.I) else "instruments"
    instruments = [{"market": m.upper(), "symbol": s.upper().zfill(5) if m.upper() == "HK" and s.isdigit() else s.upper()} for m, s in re.findall(r"\b(CN|HK|US)\s*:\s*([A-Za-z0-9.]+)", text, re.I)]
    if not instruments and context.get("stock_symbol"):
        instruments = [{"symbol": context["stock_symbol"], "market": context.get("stock_market") or "CN"}]
    duration = re.search(r"(?:未来|持续|有效|关注|盯|接下来)?\s*(\d+|两|二|一|三|四)\s*(周|星期|天|日)|(?:for|next|valid for)\s+(\d+|two|one|three)\s*(weeks?|days?)", text, re.I)
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
    channels = [name for name, pattern in {"feishu": r"飞书|feishu|lark", "telegram": r"telegram|电报", "email": r"邮件|email"}.items() if re.search(pattern, text, re.I)]
    clarification = []
    if scope == "instruments" and not instruments:
        clarification.append("instrument_lookup_required")
    return {
        "original_text": text, "evaluated_at": now.isoformat(), "instruments": instruments,
        "scope": scope, "horizon": horizon, "requested_condition_types": requested_types,
        "condition_logic": "or" if re.search(r"或者|或|\bor\b", text, re.I) else "and",
        "requested_channels": channels, "unsupported_conditions": unsupported,
        "requires_clarification": clarification,
        "capabilities": {
            "condition_types": SUPPORTED_CONDITIONS, "condition_logic": ["and", "or"],
            "expiry": True, "notification_channel_selection": True,
            "market_hours": ["trading_only", "always"], "repeat_modes": ["once", "repeat"],
            "semantics": "Stateless quote polling; thresholds are not bar-close or crossing confirmation.",
            "unsupported_conditions": list(UNSUPPORTED_CONDITIONS),
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
    changes_conditions = tool_name == "create_price_alert" or any(k in arguments for k in ("condition_group", "direction", "target_price"))
    if changes_conditions and plan["unsupported_conditions"]:
        return "Unsupported alert conditions: " + ", ".join(plan["unsupported_conditions"]) + ". Explain the limitation; do not silently substitute intraday thresholds."
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
