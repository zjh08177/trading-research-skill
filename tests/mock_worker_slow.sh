#!/usr/bin/env bash
# Stall-drill fixture (acceptance criterion 4, design-proposal.md S1): sleeps
# MOCK_WORKER_SLEEP_S seconds (default 15) with NO output before emitting the
# normal canned artifact + receipt -- but ONLY for the role named by
# MOCK_WORKER_SLEEP_ROLE (default "risk"); every other role behaves exactly
# like mock_worker.sh, so a full mock pipeline run still finishes quickly
# except for the one deliberately-stalled call.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SLEEP_S="${MOCK_WORKER_SLEEP_S:-15}"
SLEEP_ROLE="${MOCK_WORKER_SLEEP_ROLE:-risk}"

# The role has to be known BEFORE deciding whether to sleep, and stdin can
# only be read once -- so drain it into a temp file and re-feed mock_worker.sh
# from there.
PROMPT_FILE="$(mktemp -t mock-worker-slow-prompt.XXXXXX)"
trap 'rm -f "$PROMPT_FILE"' EXIT
cat > "$PROMPT_FILE"
ROLE="$(sed -n 's/^ROLE: *//p' "$PROMPT_FILE" | head -1)"

if [[ "$ROLE" == "$SLEEP_ROLE" ]]; then
  sleep "$SLEEP_S"
fi

"$HERE/mock_worker.sh" "$@" < "$PROMPT_FILE"
