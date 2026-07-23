#!/usr/bin/env bash
# mock_worker.sh — offline stand-in for ~/.agent/bin/cursor-delegate.sh.
#
# Exercises the REAL delegate contract that pipeline_driver.py depends on, so a
# mock run proves the driver's plumbing rather than bypassing it:
#   * same argv shape:  --dir DIR --mode read-only --model MODEL
#   * prompt arrives on STDIN, never argv
#   * a real receipt JSON is written and its path is announced on stderr as
#     `[cursor-delegate] receipt: <path>`
#   * promptSha256 is computed exactly the way cursor-delegate.sh computes it
#     (`printf '%s' "$PROMPT" | shasum -a 256`, i.e. over the stdin bytes with
#     trailing newlines stripped by `$(cat)`), so the driver's receipt check is
#     genuinely verified and not stubbed out.
# It calls NO model and NO network. Canned, role-valid artifacts come from
# tests/fixtures/driver/ (derived from the audited run UNH-2026-07-21-2147).
#
# The role is read from the prompt's first `ROLE: <role>` line — the driver puts
# it there for exactly this reason.
#
# Env:
#   MOCK_WORKER_DUPLICATE=<role>[,<role>...]  emit that role's artifact TWICE
#       (the sent-brief defect the accept gate exists to catch)
#   MOCK_WORKER_FAIL=<role>[,<role>...]       exit nonzero for that role
#   MOCK_WORKER_FIXTURES=<dir>                override the fixture directory
#   MOCK_WORKER_RECEIPT_DIR=<dir>             override the receipt directory
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES="${MOCK_WORKER_FIXTURES:-$HERE/fixtures/driver}"
RECEIPT_DIR="${MOCK_WORKER_RECEIPT_DIR:-${TMPDIR:-/tmp}/mock-worker-runs}"

DIR="$PWD"; MODE="write"; MODEL="mock-model"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)   DIR="${2:?--dir needs a value}"; shift 2 ;;
    --mode)  MODE="${2:?--mode needs a value}"; shift 2 ;;
    --model) MODEL="${2:?--model needs a value}"; shift 2 ;;
    --json|--worktree) shift ;;
    *) shift ;;
  esac
done
[[ -d "$DIR" ]] || { echo "[mock-worker] working dir does not exist: $DIR" >&2; exit 2; }

PROMPT="$(cat)"
[[ "$PROMPT" == *[![:space:]]* ]] || { echo "[mock-worker] empty prompt" >&2; exit 2; }

ROLE="$(printf '%s\n' "$PROMPT" | sed -n 's/^ROLE: *//p' | head -1)"
[[ -n "$ROLE" ]] || { echo "[mock-worker] prompt carries no 'ROLE:' line" >&2; exit 2; }

in_list() {  # $1=needle $2=comma list
  local IFS=,; local x
  for x in $2; do [[ "$x" == "$1" ]] && return 0; done
  return 1
}

if [[ -n "${MOCK_WORKER_FAIL:-}" ]] && in_list "$ROLE" "$MOCK_WORKER_FAIL"; then
  echo "[mock-worker] simulated failure for role $ROLE" >&2
  exit 1
fi

case "$ROLE" in
  fund|tech|sent|meanrev) FIXTURE="$FIXTURES/analyst-$ROLE.md" ;;
  bull|bear|risk)         FIXTURE="$FIXTURES/${ROLE}.md" ;;
  writer|report|qa_fix)   FIXTURE="$FIXTURES/report.md" ;;
  prose_qa)               FIXTURE="$FIXTURES/prose-qa.txt" ;;
  judge)
    # Byte-identical judge prompts by design (invariant 2), so the slot cannot be
    # read from the prompt — key the canned vote off the model slug instead, which
    # is the one thing that legitimately differs per judge.
    case "$MODEL" in
      *opus*|composer*) FIXTURE="$FIXTURES/vote-2.md" ;;
      *glm*|*grok*)     FIXTURE="$FIXTURES/vote-3.md" ;;
      *)                FIXTURE="$FIXTURES/vote-1.md" ;;
    esac ;;
  *) echo "[mock-worker] no canned artifact for role: $ROLE" >&2; exit 3 ;;
esac
[[ -f "$FIXTURE" ]] || { echo "[mock-worker] missing fixture: $FIXTURE" >&2; exit 3; }

OUT="$(mktemp -t mock-worker-out.XXXXXX)"
trap 'rm -f "$OUT"' EXIT
cat "$FIXTURE" > "$OUT"
if [[ -n "${MOCK_WORKER_DUPLICATE:-}" ]] && in_list "$ROLE" "$MOCK_WORKER_DUPLICATE"; then
  # Verbatim second emission — the exact defect 20-analyst-sent.md shipped.
  cat "$FIXTURE" >> "$OUT"
fi

mkdir -p "$RECEIPT_DIR"
RECEIPT_PATH="$RECEIPT_DIR/$(date +%s)-$$-${RANDOM}.json"
PROMPT_SHA="$(printf '%s' "$PROMPT" | shasum -a 256 | cut -d' ' -f1)"
OUTPUT_SHA="$(shasum -a 256 "$OUT" | cut -d' ' -f1)"
NOW_MS="$(($(date +%s) * 1000))"
cat > "$RECEIPT_PATH" <<JSON
{
  "cli": "mock-worker",
  "cliModel": "$MODEL",
  "dir": "$DIR",
  "mode": "$MODE",
  "role": "$ROLE",
  "promptSha256": "$PROMPT_SHA",
  "outputSha256": "$OUTPUT_SHA",
  "exitCode": 0,
  "startedAtMs": $NOW_MS,
  "durationMs": 1,
  "attempt": 1
}
JSON
echo "[cursor-delegate] receipt: $RECEIPT_PATH" >&2
cat "$OUT"
