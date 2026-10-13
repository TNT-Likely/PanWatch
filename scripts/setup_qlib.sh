#!/usr/bin/env bash
set -euo pipefail

# Dedicated runtime, never downgrade pandas/numpy in the API environment.
task_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_repo"
if command -v uv >/dev/null 2>&1; then
  uv venv .venv-qlib --python 3.12 --allow-existing
  uv pip sync --python .venv-qlib/bin/python --require-hashes requirements-qlib.txt
else
  python3.12 -m venv .venv-qlib
  .venv-qlib/bin/python -m pip install --require-hashes -r requirements-qlib.txt
fi
if [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "aarch64" ]]; then
  # Requires g++; no unlocked build-isolation dependency downloads.
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv-qlib/bin/python --no-deps --no-build-isolation --require-hashes -r requirements-qlib-arm64.txt
  else
    .venv-qlib/bin/python -m pip install --no-deps --no-build-isolation --require-hashes -r requirements-qlib-arm64.txt
  fi
fi
.venv-qlib/bin/python src/modules/qlib_research/worker.py --health
