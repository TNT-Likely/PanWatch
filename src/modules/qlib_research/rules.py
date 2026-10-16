"""Versioned, causal paper-experiment rules. No fitted model or user code.

All values are calculated from completed bars at/before the signal date.
Rules describe why a security matches; they make no win-rate/return claim.
"""

import math

VERSION = "ohlcv_rules_v1"
TEMPLATES = ("trend_breakout", "strength_continuation", "oversold_repair")


def is_rule(config):
    return config.get("algorithm") in TEMPLATES


def evaluate(bars, algorithm):
    if algorithm not in TEMPLATES:
        raise ValueError("qlib_rule_unknown")
    empty = dict(
        active=False,
        score=0.0,
        rule_version=VERSION,
        conditions={},
        values={},
        issue="qlib_rule_history_insufficient",
    )
    if len(bars) < 61:
        return empty
    recent = bars[-61:]
    if any(
        not isinstance(b.get(k), (int, float)) or not math.isfinite(b[k]) or b[k] <= 0
        for b in recent
        for k in ("close", "high", "low", "factor")
    ):
        return {**empty, "issue": "qlib_rule_prices_invalid"}
    if any(
        not isinstance(b.get("volume"), (int, float))
        or not math.isfinite(b["volume"])
        or b["volume"] < 0
        for b in recent
    ):
        return {**empty, "issue": "qlib_rule_prices_invalid"}
    close = [b["close"] * b["factor"] for b in recent]
    high = [b["high"] * b["factor"] for b in recent]
    low = [b["low"] * b["factor"] for b in recent]
    # Adjusted volume, rather than a split-induced jump in raw share count.
    volume = [b["volume"] / b["factor"] for b in recent]
    ret20 = close[-1] / close[-21] - 1
    if abs(ret20) > 1 or any(abs(b / a - 1) > 0.35 for a, b in zip(close, close[1:])):
        return {**empty, "issue": "qlib_automation_input_anomaly"}
    mean = lambda seq: sum(seq) / len(seq)
    ma5, ma20, ma60 = mean(close[-5:]), mean(close[-20:]), mean(close[-60:])
    prior_high = max(high[-21:-1])
    prior_low = min(low[-21:-1])
    volmean = mean(volume[-21:-1])
    volratio = volume[-1] / volmean if volmean > 0 else 0
    values = dict(
        return20=ret20,
        volume_ratio=volratio,
        # Display levels on the latest raw-price scale, not the arbitrary
        # absolute scale of a vendor's accumulated adjustment factors.
        close=close[-1] / recent[-1]["factor"],
        ma5=ma5 / recent[-1]["factor"],
        ma20=ma20 / recent[-1]["factor"],
        ma60=ma60 / recent[-1]["factor"],
        previous_high20=prior_high / recent[-1]["factor"],
        previous_low20=prior_low / recent[-1]["factor"],
    )
    conditions = {
        "trend_breakout": dict(
            breakout=close[-1] > prior_high, trend=ma20 > ma60, volume=volratio >= 1.2
        ),
        "strength_continuation": dict(
            trend=close[-1] > ma20 > ma60,
            momentum=0.05 <= ret20 <= 0.6,
            near_high=close[-1] >= max(high[-20:]) * 0.95,
        ),
        "oversold_repair": dict(
            oversold=-0.35 <= ret20 <= -0.1,
            repair=close[-1] > ma5 and close[-2] <= mean(close[-6:-1]),
            volume=volratio >= 1.1,
        ),
    }[algorithm]
    active = all(conditions.values()) and volume[-1] > 0
    # The leading one separates matches from nonmatches. This is only a
    # deterministic ordering key, not a probability or predicted return.
    score = (
        1
        + (ret20 if algorithm != "oversold_repair" else -ret20)
        + min(volratio, 3) / 100
        if active
        else 0.0
    )
    return dict(
        active=active,
        score=score,
        rule_version=VERSION,
        conditions=conditions,
        values=values,
        issue="",
    )


def score_snapshot(snapshot, algorithm, read_history):
    """Identical no-fit forward rules in the worker and daily scheduler."""
    rows = []
    dates, day = snapshot["calendar"][-61:], snapshot["asof_date"]
    for ref in snapshot["members"]:
        history = read_history(ref["history_id"])
        symbol, meta = ref["symbol"], ref["metadata"]
        if history["symbol"] != symbol:
            raise ValueError("qlib_history_symbol_mismatch")
        bars = [b for b in history["bars"] if dates[0] <= b["date"] <= day]
        rule = evaluate(bars, algorithm)
        if [b["date"] for b in bars] != dates:
            rule.update(active=False, score=0.0, issue="qlib_rule_history_insufficient")
        latest = bars[-1] if bars and bars[-1]["date"] == day else None
        rows.append(
            dict(
                symbol=symbol,
                market=snapshot["market"],
                currency=meta["currency"],
                raw_score=rule["score"],
                tradable=bool(latest and latest["volume"] > 0),
                paper_eligible=snapshot["market"] == "CN"
                and meta.get("board") == "MAIN"
                and not meta.get("special_treatment"),
                reference_close=latest["close"] if latest else None,
                signal_active=rule["active"],
                rule_evidence=rule,
                factor_inputs={},
                explanation_kind="fixed_rule_conditions",
            )
        )
    rows.sort(key=lambda r: (-r["raw_score"], r["symbol"]))
    for i, row in enumerate(rows, 1):
        row.update(rank=i, rank_percentile=100 * (1 - (i - 1) / max(1, len(rows) - 1)))
    return rows
