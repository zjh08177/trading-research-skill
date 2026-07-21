#!/bin/bash
# Job B + Job C daily runner (no MT access needed — pure UW + local math).
# Wired via com.mt-alpha-score.plist to run after the daily capture.
set -euo pipefail
REPO="/Users/bytedance/Work/Damn/trading-research-skill"
PY="$REPO/.venv/bin/python3"
export MT_ALPHA_DIR="${MT_ALPHA_DIR:-$HOME/trading-reports/marketterminal}"
"$PY" "$REPO/market-terminal-alpha/scripts/mt_resolve.py"
"$PY" "$REPO/market-terminal-alpha/scripts/mt_score.py"
