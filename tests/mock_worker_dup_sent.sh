#!/usr/bin/env bash
# Second-run stub: identical to mock_worker.sh except the `sent` analyst emits
# its brief TWICE — the exact defect that reached 45-judge-bundle.md:175 in the
# audited run and was voted on undisclosed. Proves the accept gate quarantines
# it instead of feeding it downstream.
set -euo pipefail
export MOCK_WORKER_DUPLICATE="sent"
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/mock_worker.sh" "$@"
