"""Real Qlib computation on synthetic data; opt-in separate worker environment."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from src.modules.qlib_research.contracts import ResearchConfig
from tests.qlib_fixture import panel

WORKER = os.environ.get("QLIB_WORKER_PYTHON")
pytestmark = pytest.mark.skipif(not WORKER, reason="Set QLIB_WORKER_PYTHON to the isolated pinned worker")


@pytest.mark.parametrize("algorithm", ["lightgbm", "ridge"])
def test_real_train_backtest_rolling_and_replay(tmp_path, algorithm):
    directory = tmp_path / "qlib"
    (directory / "datasets").mkdir(parents=True)
    data = directory / "datasets/synthetic.json"
    data.write_text(json.dumps(panel()))
    config = ResearchConfig(algorithm=algorithm, topk=3, num_boost_round=20).model_dump()
    entrypoint = Path(__file__).resolve().parents[1] / "src/modules/qlib_research/worker.py"
    results = []
    for name in ("train", "replay"):
        job = directory / "jobs" / name
        job.mkdir(parents=True)
        task = {"dataset_path": str(data), "config": config}
        if results:
            task.update(training_dataset_path=str(data), segments=results[0]["segments"], replay_fingerprint=results[0]["replay_fingerprint"])
        (job / "task.json").write_text(json.dumps(task))
        result = subprocess.run([WORKER, str(entrypoint), "--task", str(job / "task.json")], capture_output=True, text=True, timeout=180,
                                env={**os.environ, "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}, cwd=job)
        assert result.returncode == 0, result.stderr[-6000:]
        output = json.loads((job / "result.json").read_text())
        assert output["runtime"]["pyqlib"] == "0.9.7"
        assert output["data_kind"] == "synthetic"
        assert len(output["predictions"]) == 8
        assert output["metrics"]["ic_dates"] >= 20
        assert len(output["metrics"]["backtest"]["curve"]) >= 20
        assert output["metrics"]["backtest"]["paper_aligned"]
        assert output["metrics"]["backtest"]["generated_orders"] > 0
        assert output["metrics"]["backtest"]["allocation_policy"] == "topk_dropout_next_open_market_pool_target_v2"
        assert len(output["rolling"]) == 3
        assert all(fold["segments"]["valid"][1] < fold["segments"]["test"][0] for fold in output["rolling"])
        assert output["monitor"]["fraction"] == 1
        assert sorted(row["raw_score"] for row in output["predictions"])[::-1] == [row["raw_score"] for row in output["predictions"]]
        results.append(output)
    assert results[0]["replay_fingerprint"] == results[1]["replay_fingerprint"]
    assert results[0]["predictions"] == results[1]["predictions"]
