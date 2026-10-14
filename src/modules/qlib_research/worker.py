"""Isolated, fixed-code Qlib worker. Inputs and outputs are data-only JSON.

Never loads an uploaded model or imports a user-selected class. Models are trained
in this process and reconstructed from immutable dataset/config for prediction.
This trades some compute for a smaller artifact-loading trust boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys


def clean(value):
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def runtime():
    import qlib  # noqa: F401
    import lightgbm  # noqa: F401
    return {name: importlib.metadata.version(name) for name in (
        "pyqlib", "pandas", "numpy", "lightgbm", "scikit-learn", "mlflow",
    )}


def provider_from_panel(panel: dict, destination: Path):
    import numpy as np
    import pandas as pd

    frame = pd.DataFrame(panel["bars"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["date", "symbol"])
    calendar = pd.DatetimeIndex(pd.to_datetime(panel["calendar"]))
    (destination / "calendars").mkdir(parents=True)
    (destination / "instruments").mkdir()
    (destination / "calendars/day.txt").write_text("\n".join(calendar.strftime("%Y-%m-%d")) + "\n")
    # Qlib requires one exclusive interval boundary beyond the final observed
    # bar. This is not a fabricated trading session and supplies no prices.
    boundary = calendar[-1] + pd.Timedelta(days=1)
    (destination / "calendars/day_future.txt").write_text("\n".join(calendar.strftime("%Y-%m-%d")) + "\n" + boundary.strftime("%Y-%m-%d") + "\n")
    (destination / "instruments/all.txt").write_text("".join(
        f"{m['symbol']}\t{m['start']}\t{m['end']}\n" for m in panel["members"]
    ))
    for symbol, bars in frame.groupby("symbol"):
        bars = bars.set_index("date").reindex(calendar)
        first = bars["close"].dropna().iloc[0] * bars["factor"].dropna().iloc[0]
        normalized_factor = bars["factor"] / first
        values = {field: bars[field] * normalized_factor for field in ("open", "high", "low", "close")}
        values["volume"] = bars["volume"] / normalized_factor
        values["factor"] = normalized_factor
        limit = next((m.get("price_limit") for m in panel["members"] if m["symbol"] == symbol), None)
        values["limit_rate"] = bars["close"].notna().astype(float) * (limit or 0)
        # Dataset completeness controls trading availability; no forward-filled bars.
        values["change"] = values["close"].pct_change(fill_method=None)
        directory = destination / "features" / symbol.lower()
        directory.mkdir(parents=True)
        for field, series in values.items():
            np.concatenate(([0.0], series.to_numpy(dtype=float))).astype("<f4").tofile(directory / f"{field}.day.bin")
    return frame, calendar


def segments(calendar, config):
    # Warm-up features and purge both train/validation boundaries by label horizon.
    n = len(calendar)
    warm = 60
    train_end = int(n * config["train_fraction"])
    valid_end = int(n * (config["train_fraction"] + config["valid_fraction"]))
    gap = config["horizon"] + 1
    ranges = {
        "train": (warm, train_end),
        "valid": (train_end + gap, valid_end),
        "test": (valid_end + gap, n - 1),
    }
    if any(b - a < 10 for a, b in ranges.values()) or n - config["horizon"] - ranges["test"][0] < config["min_test_days"]:
        raise ValueError("qlib_time_split_insufficient")
    return {key: (calendar[a].strftime("%Y-%m-%d"), calendar[b].strftime("%Y-%m-%d")) for key, (a, b) in ranges.items()}


def metric_summary(returns):
    import numpy as np
    values = returns.dropna().astype(float)
    if values.empty:
        return {"days": 0, "return": None, "max_drawdown": None, "sharpe": None}
    equity = (1.0 + values).cumprod()
    deviation = float(values.std(ddof=1))
    return clean({
        "days": int(len(values)),
        "return": float(equity.iloc[-1] - 1),
        "max_drawdown": max(0.0, float(-(equity / equity.cummax().clip(lower=1.0) - 1).min())),
        "sharpe": float(values.mean() / deviation * np.sqrt(252)) if deviation > 0 else None,
    })


def evaluate_signal(prediction, labels):
    import pandas as pd
    joined = pd.concat([prediction.rename("score"), labels.rename("label")], axis=1).dropna()
    daily = []
    for _, group in joined.groupby(level="datetime"):
        if len(group) >= 3 and group["score"].nunique() > 1 and group["label"].nunique() > 1:
            daily.append(float(group["score"].corr(group["label"], method="spearman")))
    sequence = pd.Series(daily, dtype=float)
    deviation = float(sequence.std(ddof=1)) if len(sequence) > 1 else 0
    return clean({
        "rank_ic": float(sequence.mean()) if len(sequence) else None,
        "icir": float(sequence.mean() / deviation) if deviation > 0 else None,
        "ic_dates": int(len(sequence)), "security_days": int(len(joined)),
        "note": "Daily cross-sectional RankIC; overlapping horizons are not independent trials; ICIR is not annualized.",
    })


def portfolio_backtest(prediction, split, config, market="CN", members=None):
    """Real Qlib executor with next-bar open execution and long-only Top-K dropout."""
    from qlib.contrib.evaluate import backtest_daily
    from qlib.contrib.strategy import TopkDropoutStrategy
    import pandas as pd
    from qlib.backtest.exchange import Exchange
    lots = {m["symbol"]: m.get("lot", 100 if market == "CN" else 1) for m in (members or [])}
    class SymbolLotExchange(Exchange):
        # Qlib's strategy passes only factor to rounding after get_factor(code).
        # Set the unit for that exact code and again for the executed order.
        def get_factor(self, stock_id, *args, **kwargs):
            self.trade_unit = lots.get(stock_id, 100 if market == "CN" else 1)
            return super().get_factor(stock_id, *args, **kwargs)

        def deal_order(self, order, *args, **kwargs):
            self.trade_unit = lots.get(order.stock_id, 100 if market == "CN" else 1)
            return super().deal_order(order, *args, **kwargs)

    limit_expr = f"(Abs($open / Ref($close, 1) - 1) >= ($limit_rate - {max(0, 0.1-config['limit_threshold'])})) | ($volume <= 0)" if market == "CN" else "$volume <= 0"
    exchange = SymbolLotExchange(freq="day", start_time=split["test"][0], end_time=split["test"][1],
        codes=list(prediction.index.get_level_values("instrument").unique()), deal_price="open",
        limit_threshold=(limit_expr, limit_expr), open_cost=config["open_cost"], close_cost=config["close_cost"],
        min_cost=config["min_cost"], trade_unit=100 if market == "CN" else 1)
    strategy = TopkDropoutStrategy(
        signal=prediction, topk=config["topk"], n_drop=config["topk"],
        hold_thresh=1, only_tradable=True, risk_degree=0.9,
    )
    report, _ = backtest_daily(
        start_time=split["test"][0], end_time=split["test"][1], strategy=strategy,
        account=config["capital"], benchmark=pd.Series(0.0, index=pd.date_range(split["test"][0], split["test"][1])),
        exchange_kwargs={"exchange": exchange},
    )
    net = report["return"] - report["cost"]
    summary = metric_summary(net)
    summary["turnover_mean"] = float(report["turnover"].mean())
    equity = (1 + net).cumprod()
    summary["curve"] = [{"date": d.strftime("%Y-%m-%d"), "equity": float(v)} for d, v in equity.items()]
    summary["policy"] = f"topk_dropout_daily_next_open_90pct_cash; market={market}; current_symbol_lots; no short positions; declared fees in local currency"
    summary["market_rules_warning"] = "Current lot/limit metadata is a research assumption, not certified historical rules; CN STAR/BSE rules are conservative research approximations"
    summary["benchmark"] = "zero-return cash reference; not CSI300 excess return"
    return clean(summary)


def run(task_path: Path, return_context=False):
    import numpy as np
    import pandas as pd
    import qlib
    from qlib.config import REG_CN, REG_US
    from qlib.contrib.data.handler import Alpha158
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.contrib.model.linear import LinearModel
    from qlib.data.dataset import DatasetH

    task = json.loads(task_path.read_text())
    directory = task_path.parent.resolve()
    dataset_path = Path(task["dataset_path"]).resolve()
    if not dataset_path.is_relative_to(directory.parent.parent / "datasets"):
        raise ValueError("qlib_worker_dataset_path_invalid")
    panel = json.loads(dataset_path.read_text())
    config = task["config"]
    model_panel_path = Path(task.get("training_dataset_path", dataset_path)).resolve()
    if not model_panel_path.is_relative_to(directory.parent.parent / "datasets"):
        raise ValueError("qlib_worker_dataset_path_invalid")
    model_panel = json.loads(model_panel_path.read_text())
    market = model_panel.get("market", "CN")
    # A newer prediction snapshot may extend history, never revise training input.
    if task.get("training_dataset_path"):
        frozen = {(b["date"], b["symbol"]): b for b in model_panel["bars"]}
        current = {(b["date"], b["symbol"]): b for b in panel["bars"]}
        if any(current.get(key) != bar for key, bar in frozen.items()):
            raise ValueError("qlib_training_history_changed")
        last_frozen = max(model_panel["calendar"])
        if {key: bar for key, bar in current.items() if key[0] <= last_frozen} != frozen:
            raise ValueError("qlib_training_history_changed")
        if panel["calendar"][:len(model_panel["calendar"])] != model_panel["calendar"]:
            raise ValueError("qlib_training_calendar_changed")
        def comparable_members(members):
            return sorted([{**member, "end": min(member["end"], last_frozen), "lot": member.get("lot", 100), "currency": member.get("currency") or {"CN":"CNY","HK":"HKD","US":"USD"}[market]} for member in members], key=str)
        historical_members = [member for member in panel["members"] if member["start"] <= last_frozen]
        if comparable_members(historical_members) != comparable_members(model_panel["members"]):
            raise ValueError("qlib_training_universe_changed")
    provider = directory / "provider"
    frame, calendar = provider_from_panel(panel, provider)
    qlib.init(
        provider_uri=str(provider), region=REG_CN if market == "CN" else REG_US, kernels=1,
        expression_cache=None, dataset_cache=None,
        exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": (directory / "mlruns").as_uri(), "default_exp_name": "PanWatch"}},
    )
    training_dates = pd.DatetimeIndex(pd.to_datetime(model_panel["calendar"]))
    split = task.get("segments") or segments(training_dates, config)
    learning_split = {**split, "test": (split["test"][0], calendar[-1].strftime("%Y-%m-%d"))}
    # Membership intervals are enforced by Qlib's named instrument provider.
    instruments = "all"
    handler = Alpha158(
        instruments=instruments, start_time=calendar[0], end_time=calendar[-1],
        fit_start_time=split["train"][0], fit_end_time=split["train"][1],
        label=[f"Ref($close, -{config['horizon']}) / Ref($open, -1) - 1"],
        infer_processors=[{"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}}, {"class": "Fillna", "kwargs": {"fields_group": "feature"}}],
        learn_processors=[{"class": "DropnaLabel"}, {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}}],
    )
    dataset = DatasetH(handler=handler, segments=learning_split)
    def new_model():
        if config["algorithm"] == "lightgbm":
            return LGBModel(
            num_boost_round=config["num_boost_round"], early_stopping_rounds=15,
            learning_rate=0.05, num_leaves=15, max_depth=5, num_threads=2,
            verbosity=-1, seed=42, deterministic=True, force_col_wise=True,
            )
        return LinearModel(estimator="ridge", alpha=1.0)
    model = new_model()
    model.fit(dataset)
    prediction = model.predict(dataset, segment="test").replace([np.inf, -np.inf], np.nan).dropna()
    if prediction.empty:
        raise ValueError("qlib_prediction_empty")
    labels = dataset.prepare("test", col_set="label", data_key="raw").iloc[:, 0]
    # Evaluate only the original held-out interval, not newer forward predictions.
    original = prediction.loc[(slice(split["test"][0], split["test"][1]), slice(None))]
    replay = [(str(day.date()), symbol, round(float(value), 8)) for (day, symbol), value in original.sort_index().items()]
    replay_fingerprint = hashlib.sha256(json.dumps(replay, separators=(",", ":")).encode()).hexdigest()
    if task.get("replay_fingerprint") and task["replay_fingerprint"] != replay_fingerprint:
        raise ValueError("qlib_model_replay_changed")
    metrics = evaluate_signal(original, labels)
    metrics["backtest"] = portfolio_backtest(original, split, config, market, model_panel["members"])
    features = dataset.prepare("test", col_set="feature", data_key="infer")
    latest_date = prediction.index.get_level_values("datetime").max()
    latest = prediction.xs(latest_date, level="datetime").sort_values(ascending=False, kind="stable")
    latest_bars = frame[frame["date"] == latest_date].set_index("symbol")
    rows = []
    for rank, (symbol, score) in enumerate(latest.items(), 1):
        bars = latest_bars.loc[symbol] if symbol in latest_bars.index else None
        input_row = features.loc[(latest_date, symbol)].replace([np.inf, -np.inf], np.nan).dropna()
        rows.append({
            "symbol": symbol, "market": market, "raw_score": float(score), "rank": rank,
            "currency": next((m.get("currency") for m in panel["members"] if m["symbol"] == symbol), None) or {"CN":"CNY","HK":"HKD","US":"USD"}[market],
            "rank_percentile": 100 * (1 - (rank - 1) / max(1, len(latest) - 1)),
            "tradable": bool(bars is not None and bars["volume"] > 0),
            "paper_eligible": market == "CN" and symbol.startswith(("SH60", "SZ00")) and not any(m["special_treatment"] for m in panel["members"] if m["symbol"] == symbol),
            "reference_close": float(bars["close"]) if bars is not None else None,
            "factor_inputs": {str(k): float(v) for k, v in input_row.head(12).items()},
            "explanation_kind": "processed_model_inputs_not_causal_contributions",
        })
    base_labels = labels.reindex(original.index)
    # Transparent baseline uses the same untouched label/stock/date cohort.
    from qlib.data import D
    raw_features = D.features(D.instruments("all"), ["$close / Ref($close, 20) - 1"], start_time=split["test"][0], end_time=split["test"][1])
    momentum = raw_features.iloc[:, 0]
    momentum.index = momentum.index.reorder_levels(["datetime", "instrument"])
    baseline = evaluate_signal(momentum.reindex(original.index), base_labels)
    baseline["backtest"] = portfolio_backtest(momentum.reindex(original.index).dropna(), split, config, market, model_panel["members"])
    # Three expanding-window, independently fitted folds. Every fold's test
    # begins after validation labels have matured. Hyperparameters stay fixed.
    rolling = []
    gap = config["horizon"] + 1
    n = len(training_dates)
    width = max(config["min_test_days"] + config["horizon"], n // 10)
    for fold_end in range(n - 2 * width - 1, n, width):
        test_start = fold_end - width + 1
        valid_end = test_start - gap
        train_end = valid_end - max(20, n // 10) - gap
        if train_end <= 70:
            continue
        fold = {"train": (training_dates[60].strftime("%Y-%m-%d"), training_dates[train_end].strftime("%Y-%m-%d")),
                "valid": (training_dates[train_end + gap].strftime("%Y-%m-%d"), training_dates[valid_end].strftime("%Y-%m-%d")),
                "test": (training_dates[test_start].strftime("%Y-%m-%d"), training_dates[min(fold_end, n - 1)].strftime("%Y-%m-%d"))}
        fold_handler = Alpha158(instruments=instruments, start_time=calendar[0], end_time=calendar[-1],
            fit_start_time=fold["train"][0], fit_end_time=fold["train"][1],
            label=[f"Ref($close, -{config['horizon']}) / Ref($open, -1) - 1"],
            infer_processors=[{"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}}, {"class": "Fillna", "kwargs": {"fields_group": "feature"}}],
            learn_processors=[{"class": "DropnaLabel"}, {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}}])
        fold_dataset = DatasetH(handler=fold_handler, segments=fold)
        fold_model = new_model()
        fold_model.fit(fold_dataset)
        fold_prediction = fold_model.predict(fold_dataset, segment="test").replace([np.inf, -np.inf], np.nan).dropna()
        fold_labels = fold_dataset.prepare("test", col_set="label", data_key="raw").iloc[:, 0]
        fold_metrics = evaluate_signal(fold_prediction, fold_labels)
        fold_metrics["backtest"] = portfolio_backtest(fold_prediction, fold, config, market, model_panel["members"])
        rolling.append({"segments": fold, "model_available_after": fold["test"][0], "metrics": fold_metrics})
    asof = latest_date.strftime("%Y-%m-%d")
    eligible = {member["symbol"] for member in panel["members"] if member["start"] <= asof <= member["end"]}
    latest_features = features.xs(latest_date, level="datetime")
    monitor = {"expected": len(eligible), "predicted": len(rows), "fraction": len(rows) / max(1, len(eligible)),
               "input_abs_z_mean": float(latest_features.abs().mean().mean()),
               "input_shift_warning": bool(latest_features.abs().mean().mean() > 3),
               "method": "Robust train-fitted z magnitude; diagnostic proxy, not a significance test"}
    output = clean({
        "market": market,
        "price_basis": panel.get("price_basis","raw_execution"),
        "runtime": runtime(), "segments": split, "feature_set": "Alpha158",
        "label": "future N trading days close / next trading day open - 1; cross-sectional standardized for training",
        "metrics": metrics, "baseline": baseline, "asof_date": latest_date.strftime("%Y-%m-%d"),
        "predictions": rows, "training_days": len(training_dates),
        "data_kind": panel["data_kind"], "universe_kind": panel["universe_kind"],
        "adjustment": panel["adjustment"],
        "prediction_scale": "model output, not probability or percentage return",
        "rolling": rolling, "monitor": monitor,
        "replay_fingerprint": replay_fingerprint,
    })
    temporary = directory / "result.tmp"
    temporary.write_text(json.dumps(output, allow_nan=False, separators=(",", ":")))
    temporary.replace(directory / "result.json")
    if return_context:
        return {"model": model, "handler": handler, "panel": model_panel, "config": config,
                "split": split, "directory": directory, "provider": provider, "output": output}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--task", type=Path)
    arguments = parser.parse_args()
    if arguments.health:
        print(json.dumps(runtime()))
    elif arguments.task:
        if json.loads(arguments.task.read_text()).get("universe_manifest"):
            from universe_worker import run_universe
            run_universe(arguments.task.resolve())
        else:
            run(arguments.task.resolve())
    else:
        parser.error("--task or --health required")
