"""Trusted worker extension: one frozen market model, all ready instruments.

Imported only by the pinned worker entrypoint. No uploaded code/models.
"""

from pathlib import Path
import copy
import gc
import hashlib
import json
import re


def run_universe(task_path: Path):
    import numpy as np
    import pandas as pd
    from qlib.contrib.data.handler import Alpha158
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    from worker import run, clean

    task = json.loads(task_path.read_text())
    context = run(task_path, return_context=True)
    directory = context["directory"]
    owned_root = directory.parent.parent
    manifest_path = Path(task["universe_manifest"]).resolve()
    if not manifest_path.is_relative_to(owned_root / "snapshots"):
        raise ValueError("qlib_universe_manifest_path_invalid")
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != task["manifest_hash"]:
        raise ValueError("qlib_universe_manifest_hash_mismatch")
    snapshot = json.loads(manifest_path.read_text())
    market = snapshot["market"]
    if market != context["panel"].get("market", "CN"):
        raise ValueError("qlib_model_market_mismatch")
    from rules import is_rule
    if is_rule(context["config"]):
        from rule_worker import forward_rules
        return forward_rules(context, snapshot, owned_root)
    provider = context["provider"]
    frozen_calendar = context["panel"]["calendar"]
    calendar = frozen_calendar + [
        d for d in snapshot["calendar"] if d > frozen_calendar[-1]
    ]
    if calendar[-1] != snapshot["asof_date"]:
        raise ValueError("qlib_universe_calendar_invalid")
    frame_calendar = pd.DatetimeIndex(pd.to_datetime(calendar))
    (provider / "calendars/day.txt").write_text("\n".join(calendar) + "\n")
    (provider / "calendars/day_future.txt").write_text(
        "\n".join(calendar)
        + "\n"
        + (frame_calendar[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        + "\n"
    )
    latest_prices, latest_volume, metadata = {}, {}, {}
    frozen = {(b["date"], b["symbol"]): b for b in context["panel"]["bars"]}
    training_symbols = {m["symbol"] for m in context["panel"]["members"]}
    for ref in snapshot["members"]:
        history_id = ref["history_id"]
        if not re.fullmatch(r"[a-f0-9]{64}", history_id):
            raise ValueError("qlib_history_id_invalid")
        path = (owned_root / "history" / f"{history_id}.json").resolve()
        if not path.is_relative_to(owned_root / "history"):
            raise ValueError("qlib_history_path_invalid")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != history_id:
            raise ValueError("qlib_history_hash_mismatch")
        history = json.loads(raw)
        symbol = ref["symbol"]
        bars = [b for b in history["bars"] if calendar[0] <= b["date"] <= calendar[-1]]
        if symbol in training_symbols:
            current = {(b["date"], b["symbol"]): b for b in bars}
            if any(current.get(k) != v for k, v in frozen.items() if k[1] == symbol):
                raise ValueError("qlib_training_history_changed")
        series = pd.DataFrame(bars).set_index("date")
        series.index = pd.to_datetime(series.index)
        series = series.reindex(frame_calendar)
        first = series["close"].dropna().iloc[0] * series["factor"].dropna().iloc[0]
        factor = series["factor"] / first
        values = {key: series[key] * factor for key in ("open", "high", "low", "close")}
        values.update(
            volume=series["volume"] / factor,
            factor=factor,
            limit_rate=series["close"].notna().astype(float)
            * (ref["metadata"].get("price_limit") or 0),
        )
        values["change"] = values["close"].pct_change(fill_method=None)
        target = provider / "features" / symbol.lower()
        target.mkdir(exist_ok=True)
        for field, data in values.items():
            np.concatenate(([0.0], data.to_numpy(dtype=float))).astype("<f4").tofile(
                target / f"{field}.day.bin"
            )
        latest_prices[symbol] = (
            bars[-1]["close"]
            if bars and bars[-1]["date"] == snapshot["asof_date"]
            else None
        )
        latest_volume[symbol] = bars[-1]["volume"] if bars else 0
        metadata[symbol] = ref["metadata"]
    # Same provider, same region and active experiment. Clear only data caches;
    # reinitializing Qlib would replace its active recorder and is forbidden.
    from qlib.data.cache import H

    H.clear()
    symbols = sorted(metadata)
    rows = []
    for offset in range(0, len(symbols), 64):
        handler = Alpha158(
            instruments=symbols[offset : offset + 64],
            start_time=frame_calendar[0],
            end_time=frame_calendar[-1],
            infer_processors=copy.deepcopy(context["handler"].infer_processors),
            learn_processors=[],
            init_data=False,
        )
        handler.setup_data(init_type=DataHandlerLP.IT_LS)
        dataset = DatasetH(
            handler=handler,
            segments={"forward": (snapshot["asof_date"], snapshot["asof_date"])},
        )
        predictions = (
            context["model"]
            .predict(dataset, segment="forward")
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
        )
        features = dataset.prepare("forward", col_set="feature", data_key="infer")
        for (stamp, symbol), score in predictions.items():
            if latest_prices[symbol] is None:
                continue
            inputs = (
                features.loc[(stamp, symbol)]
                .replace([np.inf, -np.inf], np.nan)
                .dropna()
            )
            meta = metadata[symbol]
            rows.append(
                {
                    "symbol": symbol,
                    "market": market,
                    "currency": meta["currency"],
                    "raw_score": float(score),
                    "tradable": latest_volume[symbol] > 0,
                    "reference_close": latest_prices[symbol],
                    "paper_eligible": market == "CN"
                    and meta.get("board") == "MAIN"
                    and not meta.get("special_treatment"),
                    "factor_inputs": {
                        str(k): float(v) for k, v in inputs.head(12).items()
                    },
                    "explanation_kind": "processed_model_inputs_not_causal_contributions",
                }
            )
        (directory / "bulk-progress.json").write_text(
            json.dumps(
                {"completed": min(offset + 64, len(symbols)), "total": len(symbols)}
            )
        )
        del handler, dataset, predictions, features
        H.clear()
        gc.collect()
    rows.sort(key=lambda r: (-r["raw_score"], r["symbol"]))
    for i, row in enumerate(rows, 1):
        row.update(rank=i, rank_percentile=100 * (1 - (i - 1) / max(1, len(rows) - 1)))
    output = {
        **context["output"],
        "asof_date": snapshot["asof_date"],
        "predictions": rows,
        "universe_snapshot_id": snapshot["id"],
        "catalog_count": snapshot["catalog_count"],
        "training_stocks": len(training_symbols),
        "ready_stocks": len(symbols),
        "monitor": {
            "expected": len(symbols),
            "predicted": len(rows),
            "fraction": len(rows) / max(1, len(symbols)),
            "method": "All ready members; one frozen model and train-fitted processors; sharded inference",
        },
    }
    temporary = directory / "result.tmp"
    temporary.write_text(
        json.dumps(clean(output), allow_nan=False, separators=(",", ":"))
    )
    temporary.replace(directory / "result.json")
