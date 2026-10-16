"""Actual pinned Qlib fixed-rule replay; synthetic prices are engineering data."""

import json
import os
from pathlib import Path
import subprocess
import pytest
from src.modules.qlib_research.contracts import ResearchConfig
from src.modules.qlib_research.rules import VERSION, TEMPLATES
from tests.qlib_fixture import panel

WORKER = os.environ.get("QLIB_WORKER_PYTHON")
pytestmark = pytest.mark.skipif(not WORKER, reason="Requires pinned Qlib worker")


@pytest.mark.parametrize("algorithm", TEMPLATES)
def test_actual_qlib_fixed_rules_no_fit_and_replay(tmp_path, algorithm):
    root = tmp_path / "qlib"
    (root / "datasets").mkdir(parents=True)
    source = panel()
    # A steadily rising synthetic cohort provides real signal/fill assertions
    # for strength; other rules may correctly stay in cash.
    for bar in source["bars"]:
        i = source["calendar"].index(bar["date"])
        base = 20 * (1.004**i)
        bar.update(
            open=base,
            close=base,
            high=base * 1.001,
            low=base * 0.999,
            factor=1,
            volume=1000000,
        )
    path = root / "datasets/synthetic.json"
    path.write_text(json.dumps(source))
    config = ResearchConfig(algorithm=algorithm, topk=3).model_dump()
    outputs = []
    for name in ("first", "replay"):
        job = root / "jobs" / name
        job.mkdir(parents=True)
        task = dict(dataset_path=str(path), config=config)
        if outputs:
            task.update(
                segments=outputs[0]["segments"],
                replay_fingerprint=outputs[0]["replay_fingerprint"],
            )
        (job / "task.json").write_text(json.dumps(task))
        result = subprocess.run(
            [
                WORKER,
                str(
                    Path(__file__).resolve().parents[1]
                    / "src/modules/qlib_research/worker.py"
                ),
                "--task",
                str(job / "task.json"),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=job,
            env={**os.environ, "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"},
        )
        assert result.returncode == 0, result.stderr[-9000:]
        output = json.loads((job / "result.json").read_text())
        assert output["feature_set"] == VERSION and output["training_days"] == 0
        assert len(output["rolling"]) == 3
        assert all(f["method"] == "fixed_rule_no_fitting" for f in output["rolling"])
        bt = output["metrics"]["backtest"]
        assert bt["paper_aligned"] and bt["days"] >= 20
        assert len(bt["curve"]) >= 20
        assert all(
            r["explanation_kind"] == "fixed_rule_conditions"
            for r in output["predictions"]
        )
        if algorithm == "strength_continuation":
            assert bt["fill_count"] > 0 and bt["fills"][0]["quantity"] > 0
            assert bt["fills"][0]["fee"] > 0
        outputs.append(output)
    assert outputs[0]["replay_fingerprint"] == outputs[1]["replay_fingerprint"]
