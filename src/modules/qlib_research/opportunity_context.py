"""Observable frozen price/volume context, never an explanation of model causality."""

import json
import math
import re

from . import service
from .policy import paper_security_supported


def enrich(run, rows):
    if not rows:
        return rows
    grouped = {}
    snapshot_id = run.get("universe_snapshot_id")
    try:
        if snapshot_id and re.fullmatch(r"[a-f0-9]{32}", snapshot_id):
            path = service.root() / "snapshots" / f"{snapshot_id}.json"
            if path.is_symlink():
                raise ValueError("snapshot_symlink")
            snapshot = json.loads(path.read_text())
            if (
                service.content_hash({k: v for k, v in snapshot.items() if k != "id"})[
                    :32
                ]
                != snapshot_id
            ):
                raise ValueError("snapshot_hash_mismatch")
            from .universe import read_history

            refs = {r["symbol"]: r for r in snapshot["members"]}
            for row in rows:
                ref = refs.get(row["symbol"])
                if ref:
                    grouped[row["symbol"]] = read_history(ref["history_id"])["bars"]
        elif run.get("data_kind") != "synthetic":
            dataset = json.loads(service.dataset_path(run["dataset_id"]).read_text())
            expected = service.get_record(run["dataset_id"], "dataset")["content_hash"]
            if service.content_hash(dataset) != expected:
                raise ValueError("dataset_hash_mismatch")
            wanted = {r["symbol"] for r in rows}
            for bar in dataset["bars"]:
                if bar["symbol"] in wanted:
                    grouped.setdefault(bar["symbol"], []).append(bar)
    except (OSError, ValueError, KeyError, TypeError):
        # Absent/unverifiable context is explicit, not guessed from z-scores.
        grouped = {}
    result = []
    for row in rows:
        bars = sorted(
            (
                b
                for b in grouped.get(row["symbol"], [])
                if b["date"] <= run["asof_date"]
            ),
            key=lambda b: b["date"],
        )
        context = None
        try:
            if len(bars) >= 21 and bars[-1]["date"] == run["asof_date"]:
                prices = [b["close"] * b["factor"] for b in bars[-60:]]
                volume_mean = sum(b["volume"] for b in bars[-21:-1]) / 20
                context = {
                    "observations": len(prices),
                    "return_20_observations": prices[-1] / prices[-21] - 1,
                    "volume_ratio_20_observations": (
                        bars[-1]["volume"] / volume_mean if volume_mean > 0 else None
                    ),
                    "drawdown_from_60_observation_high": prices[-1] / max(prices) - 1,
                    "basis": "frozen_adjusted_close_and_observed_share_volume_not_model_contributions",
                }
                if not all(
                    v is None or math.isfinite(v)
                    for k, v in context.items()
                    if k not in ("basis",)
                ):
                    context = None
        except (ValueError, KeyError, TypeError, ZeroDivisionError):
            context = None
        reason = (
            "qlib_synthetic_execution_not_supported"
            if run.get("data_kind") == "synthetic"
            else (
                "qlib_market_execution_not_supported"
                if run.get("market", row.get("market")) != "CN"
                else (
                    "qlib_security_execution_not_supported"
                    if not paper_security_supported(row)
                    else (
                        "qlib_prediction_stale"
                        if not service.freshness(run["asof_date"])
                        else (
                            "opening_quote_unavailable"
                            if not row.get("tradable")
                            else ""
                        )
                    )
                )
            )
        )
        result.append(
            {**row, "observed_context": context, "paper_block_reason": reason}
        )
    return result
