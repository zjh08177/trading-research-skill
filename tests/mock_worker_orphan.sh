#!/usr/bin/env bash
# Orphaned-grandchild fixture (profiler defect 1). Behaves exactly like
# mock_worker.sh -- canned artifact on stdout, a real receipt line on stderr --
# but first forks a background subshell that INHERITS the stdout/stderr pipe
# write-ends and holds them open for MOCK_WORKER_ORPHAN_SLEEP_S seconds AFTER
# the wrapper itself has exited.
#
# This is the orphaned-cursor-agent class behind the original 12-minute stall:
# `proc.wait()` returns the instant the wrapper dies, but the driver's reader
# threads stay blocked in read(), because EOF on the read end needs EVERY copy
# of the write end closed. A bounded `join(timeout=...)` followed by an
# unconditional buffer read would silently return a partial stdout as the run's
# artifact, or miss a receipt line that had not been drained yet. The driver
# must instead DECLARE the drain incomplete.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SLEEP_S="${MOCK_WORKER_ORPHAN_SLEEP_S:-10}"

PROMPT_FILE="$(mktemp -t mock-worker-orphan-prompt.XXXXXX)"
trap 'rm -f "$PROMPT_FILE"' EXIT
cat > "$PROMPT_FILE"

# The grandchild writes NOTHING -- it only keeps the inherited pipe fds open,
# so the only observable effect is the missing EOF.
( sleep "$SLEEP_S" ) &

"$HERE/mock_worker.sh" "$@" < "$PROMPT_FILE"
