"""Trusted no-fit strategies evaluated with the actual Qlib backtest engine."""

import hashlib
import json
import re

if __package__:
    from .rules import VERSION, evaluate, score_snapshot
else:
    from rules import VERSION, evaluate, score_snapshot


def signal_panel(panel, algorithm):
    import pandas as pd

    calendar = panel["calendar"]
    members = {m["symbol"]: m for m in panel["members"]}
    groups = {}
    for bar in panel["bars"]:
        groups.setdefault(bar["symbol"], {})[bar["date"]] = bar
    signals, evidence = {}, {}
    for symbol, lookup in groups.items():
        ordered = []
        for day in calendar:
            if day not in lookup:
                ordered = []  # No invented/forward-filled observations.
                continue
            bar = lookup[day]
            ordered.append(bar)
            if len(ordered) < 61:
                continue
            result = evaluate(ordered[-61:], algorithm)
            member = members[symbol]
            key = (pd.Timestamp(day), symbol)
            if not member["start"] <= day <= member["end"]:
                continue
            signals[key] = result["score"]
            evidence[key] = result
    index = pd.MultiIndex.from_tuples(list(signals), names=["datetime", "instrument"])
    return pd.Series(
        list(signals.values()), index=index, dtype=float
    ).sort_index(), evidence


def forward_rules(context, snapshot, owned_root):
    """Evaluate every ready member, independent of retrospective sample budget."""
    if __package__:
        from .worker import clean
    else:
        from worker import clean

    def load_history(ident):
        if not re.fullmatch(r"[a-f0-9]{64}", ident):
            raise ValueError("qlib_history_id_invalid")
        path = owned_root / "history" / f"{ident}.json"
        if path.is_symlink():
            raise ValueError("qlib_history_path_invalid")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ident:
            raise ValueError("qlib_history_hash_mismatch")
        return json.loads(raw)

    rows = score_snapshot(snapshot, context["config"]["algorithm"], load_history)
    day = snapshot["asof_date"]
    output = {
        **context["output"],
        "asof_date": day,
        "predictions": rows,
        "universe_snapshot_id": snapshot["id"],
        "catalog_count": snapshot["catalog_count"],
        "training_stocks": len(context["panel"]["members"]),
        "ready_stocks": len(rows),
        "monitor": dict(
            expected=len(rows),
            predicted=len(rows),
            fraction=1.0,
            method="all_ready_fixed_rules_no_training_budget_limit",
        ),
    }
    temporary = context["directory"] / "result.tmp"
    temporary.write_text(
        json.dumps(clean(output), allow_nan=False, separators=(",", ":"))
    )
    temporary.replace(context["directory"] / "result.json")


def run_rules(task, panel, model_panel, config, frame, calendar, split, directory):
    import pandas as pd

    if __package__:
        from .worker import clean, evaluate_signal, portfolio_backtest, runtime
    else:
        from worker import clean, evaluate_signal, portfolio_backtest, runtime

    prediction, evidence = signal_panel(panel, config["algorithm"])
    if prediction.empty:
        raise ValueError("qlib_prediction_empty")
    lookups = {
        s: g.set_index("date").reindex(calendar) for s, g in frame.groupby("symbol")
    }
    label_rows, baseline_rows = {}, {}
    for symbol, values in lookups.items():
        adjusted = values["close"] * values["factor"]
        label = (
            adjusted.shift(-config["horizon"])
            / (values["open"] * values["factor"]).shift(-1)
            - 1
        )
        momentum = adjusted / adjusted.shift(20) - 1
        label_rows.update({(d, symbol): v for d, v in label.items()})
        baseline_rows.update({(d, symbol): v for d, v in momentum.items()})
    labels = pd.Series(label_rows).reindex(prediction.index)
    baseline_signal = pd.Series(baseline_rows).reindex(prediction.index).dropna()
    original = prediction.loc[(slice(split["test"][0], split["test"][1]), slice(None))]
    replay = [
        (str(d.date()), s, round(float(v), 8))
        for (d, s), v in original.sort_index().items()
    ]
    fingerprint = hashlib.sha256(
        json.dumps(replay, separators=(",", ":")).encode()
    ).hexdigest()
    if task.get("replay_fingerprint") and task["replay_fingerprint"] != fingerprint:
        raise ValueError("qlib_model_replay_changed")

    def report(signal, interval, rule=True):
        selected = signal.loc[(slice(*interval), slice(None))]
        metrics = evaluate_signal(selected, labels)
        bt_config = config if rule else {**config, "algorithm": "momentum_baseline"}
        metrics["backtest"] = portfolio_backtest(
            selected,
            {**split, "test": interval},
            bt_config,
            panel.get("market", "CN"),
            panel["members"],
            panel,
        )
        return metrics

    metrics = report(original, split["test"])
    baseline = report(baseline_signal, split["test"], False)
    n = len(model_panel["calendar"])
    width = max(config["min_test_days"] + config["horizon"], n // 10)
    rolling = []
    for end in range(n - 2 * width - 1, n, width):
        interval = (
            model_panel["calendar"][end - width + 1],
            model_panel["calendar"][min(end, n - 1)],
        )
        rolling.append(
            dict(
                segments={"test": interval},
                method="fixed_rule_no_fitting",
                metrics=report(prediction, interval),
            )
        )
    latest_date = prediction.index.get_level_values("datetime").max()
    raw = {(b["date"], b["symbol"]): b for b in panel["bars"]}
    rows = []
    metadata = {m["symbol"]: m for m in panel["members"]}
    for symbol, score in prediction.xs(latest_date, level="datetime").items():
        bar, meta = raw[(latest_date.strftime("%Y-%m-%d"), symbol)], metadata[symbol]
        rule = evidence[(latest_date, symbol)]
        rows.append(
            dict(
                symbol=symbol,
                market=panel.get("market", "CN"),
                currency=meta.get("currency")
                or {"CN": "CNY", "HK": "HKD", "US": "USD"}[panel.get("market", "CN")],
                raw_score=float(score),
                tradable=bar["volume"] > 0,
                paper_eligible=panel.get("market", "CN") == "CN"
                and symbol.startswith(("SH60", "SZ00"))
                and not meta["special_treatment"],
                reference_close=bar["close"],
                signal_active=rule["active"],
                rule_evidence=rule,
                factor_inputs={},
                explanation_kind="fixed_rule_conditions",
            )
        )
    rows.sort(key=lambda r: (-r["raw_score"], r["symbol"]))
    for i, row in enumerate(rows, 1):
        row.update(rank=i, rank_percentile=100 * (1 - (i - 1) / max(1, len(rows) - 1)))
    output = clean(
        dict(
            market=panel.get("market", "CN"),
            price_basis=panel.get("price_basis", "raw_execution"),
            runtime=runtime(),
            segments=split,
            feature_set=VERSION,
            rule_version=VERSION,
            label="Fixed causal rules; no training; future labels used only for retrospective evaluation",
            metrics=metrics,
            baseline=baseline,
            rolling=rolling,
            predictions=rows,
            asof_date=latest_date.strftime("%Y-%m-%d"),
            training_days=0,
            data_kind=panel["data_kind"],
            universe_kind=panel["universe_kind"],
            adjustment=panel["adjustment"],
            prediction_scale="fixed_rule_ordering_key_not_probability",
            monitor=dict(
                expected=len(metadata),
                predicted=len(rows),
                fraction=len(rows) / max(1, len(metadata)),
                method="completed_bar_fixed_rules_no_fit",
            ),
            replay_fingerprint=fingerprint,
        )
    )
    temporary = directory / "result.tmp"
    temporary.write_text(json.dumps(output, allow_nan=False, separators=(",", ":")))
    temporary.replace(directory / "result.json")
    return dict(
        panel=model_panel,
        config=config,
        split=split,
        directory=directory,
        output=output,
    )
