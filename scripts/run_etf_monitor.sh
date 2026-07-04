#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d ".venv" ]]; then
  echo "Missing .venv. Install Python dependencies before starting the monitor." >&2
  exit 1
fi

source .venv/bin/activate
exec python scripts/monitor_etf_alerts.py --interval "${ETF_ALERT_INTERVAL:-15}"
