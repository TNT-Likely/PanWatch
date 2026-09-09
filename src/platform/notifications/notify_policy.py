from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


_QUIET_RE = re.compile(r"^(?P<s>\d{1,2}:\d{2})\s*-\s*(?P<e>\d{1,2}:\d{2})$")


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":", 1)
    h = int(hh)
    m = int(mm)
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError("invalid time")
    return time(hour=h, minute=m)


@dataclass(frozen=True)
class NotifyPolicy:
    timezone: str = "UTC"
    quiet_hours: str = ""  # HH:MM-HH:MM
    retry_attempts: int = 0
    retry_backoff_seconds: float = 0.0
    dedupe_ttl_overrides: dict[str, int] | None = None

    def tzinfo(self):
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return ZoneInfo("UTC")

    def is_quiet_now(self, now: datetime | None = None) -> bool:
        q = (self.quiet_hours or "").strip()
        if not q:
            return False

        m = _QUIET_RE.match(q)
        if not m:
            return False

        start = _parse_hhmm(m.group("s"))
        end = _parse_hhmm(m.group("e"))
        tz = self.tzinfo()
        dt = now.astimezone(tz) if now else datetime.now(tz)
        t = dt.time()

        if start == end:
            return True  # treat as always quiet

        if start < end:
            return start <= t < end
        # crosses midnight
        return t >= start or t < end

    def dedupe_ttl_minutes(self, agent_name: str, default: int) -> int:
        overrides = self.dedupe_ttl_overrides or {}
        try:
            v = overrides.get(agent_name)
            if v is None:
                return default
            return int(v)
        except Exception:
            return default


def parse_dedupe_overrides(value: str) -> dict[str, int]:
    raw = (value or "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in obj.items():
        if not isinstance(k, str):
            continue
        try:
            out[k] = int(v)
        except Exception:
            continue
    return out
