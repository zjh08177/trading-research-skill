#!/usr/bin/env bash
# Deadlock-proof fixture (profiler S1). Floods BOTH stdout and stderr with
# well over the ~64 KB default OS pipe buffer -- stderr first (mirrors
# cursor-agent streaming progress there before the receipt line), then
# stdout -- and only THEN exits. If pipeline_driver.py's reader threads ever
# stop draining one pipe while the other fills, the child blocks writing to
# it and the run hangs forever; this fixture exists to prove that doesn't
# happen (tests/test_profiler.py).
set -euo pipefail

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

# >64 KB on stderr BEFORE the receipt line -- the pipe a naive
# read-after-communicate() implementation would starve first.
head -c 200000 /dev/zero | tr '\0' 'e' >&2
printf '\n' >&2

RECEIPT_DIR="${MOCK_WORKER_RECEIPT_DIR:-${TMPDIR:-/tmp}/mock-worker-bigoutput}"
mkdir -p "$RECEIPT_DIR"
RECEIPT_PATH="$RECEIPT_DIR/$(date +%s)-$$-${RANDOM}.json"

OUT="$(mktemp -t mock-worker-bigoutput-out.XXXXXX)"
trap 'rm -f "$OUT"' EXIT
{
  echo "## Bull case"
  head -c 200000 /dev/zero | tr '\0' 'o'
  echo
  echo "KEY POINTS: big output, proves the pipes never deadlock"
} > "$OUT"

PROMPT_SHA="$(printf '%s' "$PROMPT" | shasum -a 256 | cut -d' ' -f1)"
OUTPUT_SHA="$(shasum -a 256 "$OUT" | cut -d' ' -f1)"
NOW_MS="$(($(date +%s) * 1000))"
cat > "$RECEIPT_PATH" <<JSON
{
  "cli": "mock-worker-bigoutput",
  "cliModel": "$MODEL",
  "dir": "$DIR",
  "mode": "$MODE",
  "promptSha256": "$PROMPT_SHA",
  "outputSha256": "$OUTPUT_SHA",
  "exitCode": 0,
  "startedAtMs": $NOW_MS,
  "durationMs": 1,
  "attempt": 1
}
JSON

# More stderr noise AFTER the receipt line too -- the reader thread must
# keep draining stderr to EOF, not stop the instant it sees the receipt.
head -c 200000 /dev/zero | tr '\0' 'f' >&2
printf '\n[cursor-delegate] receipt: %s\n' "$RECEIPT_PATH" >&2

cat "$OUT"
