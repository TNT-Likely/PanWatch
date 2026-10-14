"""Real pinned Qlib models on synthetic prices, not market-performance evidence."""

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
from src.modules.qlib_research.contracts import ResearchConfig
from tests.qlib_fixture import panel

WORKER = os.environ.get("QLIB_WORKER_PYTHON")
pytestmark = pytest.mark.skipif(
    not WORKER, reason="Requires pinned separate Qlib runtime"
)


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_same_frozen_model_predicts_entire_pool_beyond_training_cohort(
    tmp_path, market
):
    root = tmp_path / "qlib"
    for name in ("datasets", "history", "snapshots"):
        (root / name).mkdir(parents=True)
    cohort = panel(market=market, symbols=8)
    training_path = root / "datasets" / "synthetic.json"
    training_path.write_text(json.dumps(cohort))
    config = ResearchConfig(topk=3, num_boost_round=10).model_dump()
    entrypoint = (
        Path(__file__).resolve().parents[1] / "src/modules/qlib_research/worker.py"
    )
    env = {**os.environ, "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}
    job = root / "jobs" / "train"
    job.mkdir(parents=True)
    task = {"dataset_path": str(training_path), "config": config}
    (job / "task.json").write_text(json.dumps(task))
    result = subprocess.run(
        [WORKER, str(entrypoint), "--task", str(job / "task.json")],
        cwd=job,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr[-6000:]
    original = json.loads((job / "result.json").read_text())
    full = panel(market=market, symbols=160)
    grouped = {}
    training_symbols = {m["symbol"] for m in cohort["members"]}
    for b in full["bars"]:
        if b["symbol"] not in training_symbols:
            grouped.setdefault(b["symbol"], []).append(b)
    for b in cohort["bars"]:
        grouped.setdefault(b["symbol"], []).append(b)
    refs = []
    for symbol, bars in grouped.items():
        history = {"symbol": symbol, "bars": bars}
        raw = json.dumps(history, sort_keys=True, separators=(",", ":")).encode()
        ident = hashlib.sha256(raw).hexdigest()
        (root / "history" / f"{ident}.json").write_bytes(raw)
        refs.append(
            {
                "symbol": symbol,
                "history_id": ident,
                "metadata": {
                    "currency": {"CN": "CNY", "HK": "HKD", "US": "USD"}[market],
                    "board": "MAIN",
                    "lot": 100 if market != "US" else 1,
                    "price_limit": 0.1 if market == "CN" else None,
                    "special_treatment": False,
                },
            }
        )
    manifest = {
        "id": "a" * 32,
        "market": market,
        "calendar": full["calendar"],
        "asof_date": full["calendar"][-1],
        "members": refs,
        "catalog_count": 160,
    }
    path = root / "snapshots" / ("a" * 32 + ".json")
    path.write_text(json.dumps(manifest))
    job = root / "jobs" / "bulk"
    job.mkdir(parents=True)
    task.update(
        segments=original["segments"],
        replay_fingerprint=original["replay_fingerprint"],
        universe_manifest=str(path),
        manifest_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    (job / "task.json").write_text(json.dumps(task))
    result = subprocess.run(
        [WORKER, str(entrypoint), "--task", str(job / "task.json")],
        cwd=job,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-8000:]
    output = json.loads((job / "result.json").read_text())
    assert output["training_stocks"] == 8 and output["ready_stocks"] == 160
    assert len(output["predictions"]) == 160 and output["monitor"]["fraction"] == 1
    assert output["replay_fingerprint"] == original["replay_fingerprint"]
    original_scores = {r["symbol"]: r["raw_score"] for r in original["predictions"]}
    for row in output["predictions"]:
        assert (
            row["market"] == market
            and row["currency"] == {"CN": "CNY", "HK": "HKD", "US": "USD"}[market]
        )
        if row["symbol"] in original_scores:
            assert row["raw_score"] == pytest.approx(
                original_scores[row["symbol"]], abs=1e-10
            )
        if market != "CN":
            assert row["paper_eligible"] is False
    assert [r["rank"] for r in output["predictions"]] == list(range(1, 161))
