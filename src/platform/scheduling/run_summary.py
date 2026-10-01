"""Recognize the fixed legacy summary format of an idle single-stock poll."""

import re


def is_idle_single_summary(result: str | None) -> bool:
    # Require the entire scheduler summary, never a prefix of a human report.
    match = re.fullmatch(r"single mode executed 0, skipped ([0-9]+), total ([0-9]+)", result or "")
    return bool(match and int(match[1]) == int(match[2]))
