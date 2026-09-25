#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://localhost:8000}"
WITH_AGENT_SMOKE=0

for arg in "$@"; do
  case "$arg" in
    --with-agent-smoke)
      WITH_AGENT_SMOKE=1
      ;;
    *)
      echo "Unknown arg: $arg"
      echo "Usage: $0 [--with-agent-smoke]"
      exit 2
      ;;
  esac
done

run_step() {
  local title="$1"
  shift
  echo
  echo "==> $title"
  "$@"
}

run_step "Run backend unit tests" \
  python -m unittest discover -s tests -p 'test_*.py'

run_step "Compile Python files" \
  sh -c "python -m py_compile \$(rg --files src tests server.py | rg '\\.py$')"

run_step "Build frontend" \
  pnpm -C frontend build

if [[ "$WITH_AGENT_SMOKE" -eq 1 ]]; then
  echo
  echo "==> Agent smoke checks via API (${BASE_URL})"

  run_step "Check agents health" \
    curl -fsS "${BASE_URL}/api/agents/health" >/dev/null

  AGENTS=(
    "daily_report"
    "premarket_outlook"
    "chart_analyst"
    "intraday_monitor"
  )

  for agent in "${AGENTS[@]}"; do
    run_step "Trigger agent: ${agent}" \
      curl -fsS -X POST "${BASE_URL}/api/agents/${agent}/trigger" >/dev/null
  done

  run_step "Validate latest run status" \
    python - "$BASE_URL" <<'PY'
import json
import sys
from urllib.request import urlopen

base = sys.argv[1]
agents = [
    "daily_report",
    "premarket_outlook",
    "chart_analyst",
    "intraday_monitor",
]
errors = []
for agent in agents:
    with urlopen(f"{base}/api/agents/{agent}/history") as resp:
        rows = json.load(resp)
    if not rows:
        errors.append(f"{agent}: no history")
        continue
    status = (rows[0] or {}).get("status")
    if status != "success":
        errors.append(f"{agent}: latest status={status}")
if errors:
    print("Agent smoke check failed:")
    for err in errors:
        print(f"- {err}")
    sys.exit(1)
print("Agent smoke check passed")
PY
fi

echo
echo "All pre-release checks passed."
