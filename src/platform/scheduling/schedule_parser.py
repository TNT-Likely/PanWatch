"""Schedule parsing helpers.

We accept a 5-part cron in the UI/config: "min hour day month day_of_week".

Important: day_of_week numeric values follow POSIX cron semantics:
- 0 or 7 = Sunday
- 1 = Monday ... 6 = Saturday

APScheduler's CronTrigger uses a different numeric mapping (0=Monday .. 6=Sunday).
So we normalize numeric day_of_week fields before creating CronTrigger.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


_HAS_ALPHA_RE = re.compile(r"[a-zA-Z]")


def _compress_ints_to_cron_ranges(values: Iterable[int]) -> str:
    nums = sorted(set(values))
    if not nums:
        return ""

    ranges: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        if start == prev:
            ranges.append(str(start))
        else:
            ranges.append(f"{start}-{prev}")
        start = prev = n

    if start == prev:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{prev}")

    return ",".join(ranges)


def _expand_posix_cron_dow_token(token: str) -> set[int]:
    """Expand a single POSIX-cron day_of_week token into {0..6} (0=Sun).

    Supports: *, n, a-b, */s, a-b/s, and comma lists are handled by the caller.
    """

    token = token.strip()
    if not token:
        raise ValueError("empty token")

    step = 1
    base = token
    if "/" in token:
        base, step_str = token.split("/", 1)
        step = int(step_str)
        if step <= 0:
            raise ValueError("step must be positive")

    if base in ("*", "?"):
        values = list(range(0, 7))
    elif "-" in base:
        a_str, b_str = base.split("-", 1)
        a = int(a_str)
        b = int(b_str)

        # POSIX cron allows 0 or 7 as Sunday.
        if a == 7:
            a = 0
        if b == 7:
            # Special-case ranges ending at 7 (Sunday).
            if a == 0:
                values = list(range(0, 7))
            else:
                values = list(range(a, 7)) + [0]
        elif a <= b:
            values = list(range(a, b + 1))
        else:
            # Non-standard wrap range (e.g., 5-1). Keep it usable.
            values = list(range(a, 7)) + list(range(0, b + 1))
    else:
        n = int(base)
        if n == 7:
            n = 0
        values = [n]

    # Normalize into 0..6 only
    values = [v for v in values if 0 <= v <= 6]
    return set(values[::step])


def normalize_cron_day_of_week_field(day_of_week: str) -> str:
    """Normalize POSIX-cron numeric day_of_week to APScheduler semantics.

    If the field contains alpha weekdays (mon-fri) we leave it untouched.
    """

    field = (day_of_week or "").strip()
    if not field or field in ("*", "?"):
        return field or "*"

    # If it contains weekday names, assume APScheduler-compatible already.
    if _HAS_ALPHA_RE.search(field):
        return field

    # Expand POSIX cron day-of-week into a concrete set, then map to APS.
    try:
        posix_set: set[int] = set()
        for part in field.split(","):
            posix_set |= _expand_posix_cron_dow_token(part)
    except Exception:
        # If parsing fails, do not risk changing semantics.
        return field

    # Map POSIX (0=Sun..6=Sat) -> APS (0=Mon..6=Sun)
    aps_set = set()
    for d in posix_set:
        aps_set.add(6 if d == 0 else d - 1)

    if aps_set == set(range(0, 7)):
        return "*"
    return _compress_ints_to_cron_ranges(aps_set)


def parse_cron(cron: str, timezone: str = "UTC") -> CronTrigger:
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(f"无效的 cron 表达式: {cron}")

    dow = normalize_cron_day_of_week_field(parts[4])
    return CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=dow,
        timezone=timezone,
    )


def parse_interval(expr: str) -> IntervalTrigger:
    value = expr.replace("interval:", "")
    if value.endswith("s"):
        seconds = int(value[:-1])
        return IntervalTrigger(seconds=seconds)
    if value.endswith("m"):
        minutes = int(value[:-1])
        return IntervalTrigger(minutes=minutes)
    if value.endswith("h"):
        hours = int(value[:-1])
        return IntervalTrigger(hours=hours)
    raise ValueError(f"无效的 interval 表达式: {expr}")


def parse_schedule(schedule: str, timezone: str = "UTC"):
    if schedule.startswith("interval:"):
        return parse_interval(schedule)
    return parse_cron(schedule, timezone=timezone)


def preview_schedule(
    schedule: str,
    count: int = 5,
    timezone: str = "UTC",
    start: datetime | None = None,
) -> list[datetime]:
    """Return next N run times for a schedule.

    Uses the same parsing rules as the scheduler.
    """

    if count <= 0:
        return []

    trigger = parse_schedule(schedule, timezone=timezone)
    tz = ZoneInfo(timezone)
    now = start.astimezone(tz) if start else datetime.now(tz)

    out: list[datetime] = []
    prev = None
    current = now
    for _ in range(count):
        nxt = trigger.get_next_fire_time(prev, current)
        if not nxt:
            break
        out.append(nxt)
        prev = nxt
        current = nxt
    return out


def count_runs_within(
    schedule: str,
    *,
    start: datetime,
    end: datetime,
    timezone: str = "UTC",
    max_iters: int = 20000,
) -> int:
    """Count fire times within (start, end]."""
    if not schedule or end <= start:
        return 0

    trigger = parse_schedule(schedule, timezone=timezone)

    count = 0
    prev = None
    current = start
    for _ in range(max_iters):
        nxt = trigger.get_next_fire_time(prev, current)
        if not nxt:
            break
        if nxt > end:
            break
        count += 1
        prev = nxt
        current = nxt
    return count
