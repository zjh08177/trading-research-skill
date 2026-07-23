#!/usr/bin/env bash
# Stall-THEN-RESUME fixture (profiler defect 5). For the role named by
# MOCK_WORKER_STALL_ROLE (default "risk") it:
#   1. writes a few bytes to stderr IMMEDIATELY (t≈0)  -> truthful ttfa_ms
#   2. goes completely silent for MOCK_WORKER_STALL_SLEEP_S seconds -> a stall
#      episode the driver must detect while the call is still running
#   3. writes a few more bytes to stderr              -> renewed activity, so
#      `worker-resume` MUST fire
#   4. delegates to mock_worker.sh for the normal artifact + receipt
#
# Every write is well under one read chunk. A reader using a buffered
# `read(4096)` (read-until-full) sees none of it until EOF, so `last_activity`
# never moves mid-call: `worker-resume` can never fire and `ttfa_ms` reports the
# END of the call instead of its first byte. That is exactly what this fixture
# isolates -- unlike mock_worker_bigoutput.sh, which never goes silent at all.
#
# The early/late chirps go to STDERR on purpose: stdout IS the artifact, and
# cursor-agent genuinely streams its progress to stderr (the same reason the
# design proposal treats stderr silence as a real stall signal).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SLEEP_S="${MOCK_WORKER_STALL_SLEEP_S:-4}"
STALL_ROLE="${MOCK_WORKER_STALL_ROLE:-risk}"

PROMPT_FILE="$(mktemp -t mock-worker-stall-resume-prompt.XXXXXX)"
trap 'rm -f "$PROMPT_FILE"' EXIT
cat > "$PROMPT_FILE"
ROLE="$(sed -n 's/^ROLE: *//p' "$PROMPT_FILE" | head -1)"

if [[ "$ROLE" == "$STALL_ROLE" ]]; then
  printf '[stall-resume] starting\n' >&2
  sleep "$SLEEP_S"
  printf '[stall-resume] resuming\n' >&2
fi

"$HERE/mock_worker.sh" "$@" < "$PROMPT_FILE"
