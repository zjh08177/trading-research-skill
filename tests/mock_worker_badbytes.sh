#!/usr/bin/env bash
# Bad-byte fixture (profiler defect 3). Emits an INVALID UTF-8 sequence in the
# middle of both stdout and stderr, and keeps writing afterwards -- including
# the `[cursor-delegate] receipt:` line, which lands on stderr AFTER the bad
# bytes.
#
# With `Popen(text=True)` the decode is strict and happens inside the reader
# thread, so the bad sequence raises UnicodeDecodeError there; the old
# `except (ValueError, OSError): pass` then stopped the drain and the caller
# could not tell that from a clean EOF. Everything after the bad bytes was lost
# with zero signal: the artifact tail silently vanished, and the receipt line
# went missing so a real model call was misclassified `no-receipt`.
#
# Reading bytes and decoding once with errors="replace" makes that impossible:
# the replacement character is visible in the artifact, the tail survives, and
# the receipt is found.
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

RECEIPT_DIR="${MOCK_WORKER_RECEIPT_DIR:-${TMPDIR:-/tmp}/mock-worker-badbytes}"
mkdir -p "$RECEIPT_DIR"
RECEIPT_PATH="$RECEIPT_DIR/$(date +%s)-$$-${RANDOM}.json"

OUT="$(mktemp -t mock-worker-badbytes-out.XXXXXX)"
trap 'rm -f "$OUT"' EXIT
{
  echo "## Bull case"
  printf 'valid text, then an invalid UTF-8 sequence: '
  printf '\xff\xfe\x80'
  printf ' TAIL-MARKER-AFTER-BAD-BYTES\n'
  echo "KEY POINTS: every byte written after the bad sequence must survive"
} > "$OUT"

# Bad bytes on stderr BEFORE the receipt line -- the drain has to keep going or
# the receipt is never seen and the call is called a no-receipt.
printf 'stderr noise with bad bytes: ' >&2
printf '\xff\xfe\x80' >&2
printf '\n' >&2

PROMPT_SHA="$(printf '%s' "$PROMPT" | shasum -a 256 | cut -d' ' -f1)"
OUTPUT_SHA="$(shasum -a 256 "$OUT" | cut -d' ' -f1)"
NOW_MS="$(($(date +%s) * 1000))"
cat > "$RECEIPT_PATH" <<JSON
{
  "cli": "mock-worker-badbytes",
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
echo "[cursor-delegate] receipt: $RECEIPT_PATH" >&2

cat "$OUT"
