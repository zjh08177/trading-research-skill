#!/usr/bin/env bash
# Wrapper-overhead drill fixture (acceptance criterion 5, design-proposal.md
# S2): sleeps MOCK_WORKER_WRAPPER_SLEEP_S seconds (default 40, ONLY for the
# role named by MOCK_WORKER_WRAPPER_ROLE, default "risk") then emits a normal
# canned artifact but a receipt that claims durationMs=1000 -- i.e. the
# driver-observed wall clock vastly exceeds what the receipt says the model
# spent, exactly what wrapper-side stall (guard code, prompt ingestion) looks
# like from the receipt's point of view.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SLEEP_S="${MOCK_WORKER_WRAPPER_SLEEP_S:-40}"
SLEEP_ROLE="${MOCK_WORKER_WRAPPER_ROLE:-risk}"
FIXTURES="${MOCK_WORKER_FIXTURES:-$HERE/fixtures/driver}"
RECEIPT_DIR="${MOCK_WORKER_RECEIPT_DIR:-${TMPDIR:-/tmp}/mock-worker-wrapper-lag}"

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

PROMPT="$(cat)"
ROLE="$(printf '%s\n' "$PROMPT" | sed -n 's/^ROLE: *//p' | head -1)"
[[ -n "$ROLE" ]] || { echo "[mock-worker-wrapper-lag] prompt carries no 'ROLE:' line" >&2; exit 2; }

if [[ "$ROLE" == "$SLEEP_ROLE" ]]; then
  sleep "$SLEEP_S"
fi

case "$ROLE" in
  fund|tech|sent|meanrev) FIXTURE="$FIXTURES/analyst-$ROLE.md" ;;
  bull|bear|risk)         FIXTURE="$FIXTURES/${ROLE}.md" ;;
  writer|report|qa_fix)   FIXTURE="$FIXTURES/report.md" ;;
  prose_qa)               FIXTURE="$FIXTURES/prose-qa.txt" ;;
  judge)                  FIXTURE="$FIXTURES/vote-1.md" ;;
  *) echo "[mock-worker-wrapper-lag] no canned artifact for role: $ROLE" >&2; exit 3 ;;
esac
[[ -f "$FIXTURE" ]] || { echo "[mock-worker-wrapper-lag] missing fixture: $FIXTURE" >&2; exit 3; }

mkdir -p "$RECEIPT_DIR"
RECEIPT_PATH="$RECEIPT_DIR/$(date +%s)-$$-${RANDOM}.json"
PROMPT_SHA="$(printf '%s' "$PROMPT" | shasum -a 256 | cut -d' ' -f1)"
OUTPUT_SHA="$(shasum -a 256 "$FIXTURE" | cut -d' ' -f1)"
NOW_MS="$(($(date +%s) * 1000))"
cat > "$RECEIPT_PATH" <<JSON
{
  "cli": "mock-worker-wrapper-lag",
  "cliModel": "$MODEL",
  "dir": "$DIR",
  "mode": "$MODE",
  "promptSha256": "$PROMPT_SHA",
  "outputSha256": "$OUTPUT_SHA",
  "exitCode": 0,
  "startedAtMs": $NOW_MS,
  "durationMs": 1000,
  "attempt": 1
}
JSON
echo "[cursor-delegate] receipt: $RECEIPT_PATH" >&2
cat "$FIXTURE"
