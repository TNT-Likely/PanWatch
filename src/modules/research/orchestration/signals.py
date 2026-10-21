"""Deterministic intraday changes and successful-run baselines, scoped by market."""
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import threading
from zoneinfo import ZoneInfo

from pan_investment_research import ResearchSignal

from src.modules.strategy.intraday_event_gate import adaptive_price_threshold
from src.platform.persistence.json_store import read_json, write_json_atomic

_LOCK = threading.Lock()


def _path():
    return str(Path(os.environ.get("DATA_DIR", "./data")) / "state/intraday_research_signals.json")


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def published(item, now, market):
    value = item.get("time") or item.get("publish_time") or item.get("published_at")
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo({"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}[market]))
        return value.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def event_id(item):
    title = re.sub(r"\s+", "", str(item.get("title") or "")).lower()
    body = str(item.get("content") or item.get("summary") or "")[:600]
    return hashlib.sha256((title + body if title else str(item.get("url") or "")).encode()).hexdigest()


def material(item):
    return (number(item.get("importance")) or 0) >= 2


def inspect_changes(key, pack, positions, now, agent, *, event_only=True):
    market = key.split(":", 1)[0]
    day = now.astimezone(ZoneInfo({"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}.get(market, "UTC"))).date().isoformat()
    with _LOCK:
        state = read_json(_path(), default={})
    previous = state.get(key, {}) if isinstance(state, dict) else {}
    if previous.get("day") != day:
        previous = {}
    quote, tech = pack.quote, pack.technical or {}
    cp, price = number(getattr(quote, "change_pct", None)), number(getattr(quote, "current_price", None))
    vr = number(tech.get("volume_ratio"))
    styles = [getattr(p, "trading_style", "swing") for p in positions]
    style_floor = min(({"short": 2, "swing": 3, "long": 5}.get(style, 3) for style in styles), default=0)
    threshold = adaptive_price_threshold(number(tech.get("atr_pct")), max(agent.price_alert_threshold, style_floor))
    technical = []
    old_cp, old_vr, old_price = previous.get("change_pct"), previous.get("volume_ratio"), previous.get("price")
    if cp is not None and threshold > 0 and abs(cp) >= threshold and (
        old_cp is None or abs(old_cp) < threshold or cp * old_cp < 0 or abs(cp - old_cp) >= max(.5, threshold / 2)
    ):
        technical.append("atr_price_change")
    if vr is not None and agent.volume_alert_ratio > 0 and vr >= agent.volume_alert_ratio and (
        old_vr is None or old_vr < agent.volume_alert_ratio or vr - old_vr >= .5
    ):
        technical.append("volume_change")
    signature = {k: tech.get(k) for k in ("trend", "macd_status", "rsi_status", "kdj_status", "boll_status", "kline_pattern")}
    if previous.get("technical") and signature != previous["technical"]:
        technical.append("technical_state_changed")
    for level, reason in (("support", "support_broken"), ("resistance", "resistance_broken")):
        value = number(tech.get(level))
        if value and price and old_price and ((old_price >= value > price) if level == "support" else (old_price <= value < price)):
            technical.append(reason)
    warnings = []
    for p in positions:
        if price and p.cost_price > 0:
            pnl = (price / p.cost_price - 1) * 100
            if pnl <= agent.stop_loss_warning:
                warnings.append(f"{p.account_id}:stop_loss")
            elif pnl >= agent.take_profit_warning:
                warnings.append(f"{p.account_id}:take_profit")
    if set(warnings) - set(previous.get("position_warnings", [])):
        technical.append("position_risk_changed")
    items = [*(pack.news.items if pack.news else []), *(pack.events.items if pack.events else [])]
    seen = set(previous.get("events", []))
    new_events = [i for i in items if event_id(i) not in seen and (t := published(i, now, market)) is not None
                  and now - timedelta(hours=24) <= t <= now]
    signals = []
    if technical or not event_only:
        signals.append(ResearchSignal(stock_key=key, domain="technical", reasons=technical or ["explicit_continuous_analysis"], observed_at=now))
    if new_events:
        signals.append(ResearchSignal(stock_key=key, domain="events", reasons=["new_captured_event"], observed_at=now,
            material=any(material(i) for i in new_events)))
    observation = {"day": day, "change_pct": cp, "price": price, "volume_ratio": vr, "technical": signature,
        "position_warnings": warnings, "events": list(dict.fromkeys([*(event_id(i) for i in items if (t := published(i, now, market)) is None or t <= now), *previous.get("events", [])]))[:200]}
    context = {"atr_pct": number(tech.get("atr_pct")), "adaptive_price_threshold": threshold, "style_price_floor": style_floor,
        "volume_threshold": agent.volume_alert_ratio, "stop_loss_warning": agent.stop_loss_warning,
        "take_profit_warning": agent.take_profit_warning, "position_warnings": warnings}
    return signals, observation, context


def acknowledge(observations):
    """Advance only after a successful graph or a no-signal baseline; failures can retry."""
    if not observations:
        return
    with _LOCK:
        state = read_json(_path(), default={})
        state = state if isinstance(state, dict) else {}
        state.update(observations)
        write_json_atomic(_path(), state)
