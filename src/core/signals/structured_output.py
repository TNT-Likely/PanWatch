from __future__ import annotations

import json


ALLOWED_ACTIONS = {
    "buy",
    "add",
    "reduce",
    "sell",
    "hold",
    "watch",
    "alert",
    "avoid",
}


TAG_START = "<!--PANWATCH_JSON-->"
TAG_END = "<!--/PANWATCH_JSON-->"


def try_parse_action_json(text: str) -> dict | None:
    """Parse JSON-only output. Returns dict on success."""
    raw = (text or "").strip()
    if not raw:
        return None

    # Allow fenced code blocks (```json ... ```)
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[0].lstrip().startswith("```"):
            if lines[-1].strip().startswith("```"):
                raw = "\n".join(lines[1:-1]).strip()
        else:
            raw = raw.strip("`").strip()
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    action = (obj.get("action") or "").strip()
    if action and action not in ALLOWED_ACTIONS:
        return None
    return obj


def try_extract_tagged_json(
    text: str, *, start: str = TAG_START, end: str = TAG_END
) -> dict | None:
    """Extract a tagged JSON object from a larger text.

    Expected format at the end of the response:
    <!--PANWATCH_JSON-->
    { ... }
    <!--/PANWATCH_JSON-->
    """

    raw = text or ""
    i = raw.rfind(start)
    if i < 0:
        return None
    j = raw.rfind(end)
    if j < 0 or j <= i:
        return None
    payload = raw[i + len(start) : j].strip()
    if not payload:
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def strip_tagged_json(text: str, *, start: str = TAG_START, end: str = TAG_END) -> str:
    """Remove tagged JSON block from text (if present)."""
    raw = text or ""
    i = raw.rfind(start)
    if i < 0:
        return raw
    j = raw.rfind(end)
    if j < 0 or j <= i:
        return raw
    return (raw[:i] + raw[j + len(end) :]).strip()
