"""Canonical recommendation directions, separate from attention/review state."""

from __future__ import annotations

import re


ACTION_LABELS = {
    "buy": "买入", "add": "加仓", "reduce": "减仓", "sell": "卖出",
    "hold": "持有", "watch": "观望", "avoid": "回避",
}
ACTION_LABELS_EN = {
    "buy": "Buy", "add": "Add", "reduce": "Reduce", "sell": "Sell",
    "hold": "Hold", "watch": "Watch", "avoid": "Avoid",
}
ACTION_ALIASES = {
    "overweight": "add", "increase": "add", "underweight": "reduce",
    "decrease": "reduce", "build": "buy", "neutral": "watch",
}
ATTENTION_ACTIONS = {"buy", "add", "reduce", "sell", "avoid"}


def normalize_suggestion(suggestion: dict, *, agent_name: str = "") -> dict:
    """Also recover older TA records whose five ratings were collapsed to three.

    REVIEW never means hold. Legacy alert is an attention state with no trade
    direction; retain that state when returning the canonical watch direction.
    Agent-specific notification choices are kept independently of the direction.
    """
    result = dict(suggestion)
    raw = str(result.get("action") or "").strip().lower()
    label = str(result.get("action_label") or "").strip()
    rating = str(result.get("rating_raw") or "").strip().lower()
    review = bool(result.get("review_required")) or result.get("status") == "review"
    review = review or raw == "review" or rating == "review" or bool(re.search(r"待.*复核|review required", label, re.I))
    attention = bool(result.get("attention_required")) or raw == "alert"

    if agent_name == "tradingagents" or result.get("agent_name") == "tradingagents":
        if rating in ACTION_LABELS or rating in ACTION_ALIASES:
            raw = rating
        elif re.search(r"增持|overweight", label, re.I):
            raw = "add"
        elif re.search(r"减持|underweight", label, re.I):
            raw = "reduce"

    action = ACTION_ALIASES.get(raw, raw)
    if action == "alert" or review:
        action = "watch"
    if action not in ACTION_LABELS:
        action = "watch"
        review = True  # An unrecognized answer is not an actionable recommendation.

    result.update(
        action=action,
        action_label=ACTION_LABELS[action],
        status="review" if review else "ready",
        review_required=review,
        attention_required=attention,
        should_alert=bool(result.get("should_alert", action in ATTENTION_ACTIONS)) or attention or review,
    )
    return result


def suggestion_state(suggestion: dict) -> dict:
    return {key: suggestion[key] for key in (
        "status", "review_required", "attention_required", "should_alert", "rating_raw",
    ) if key in suggestion}
