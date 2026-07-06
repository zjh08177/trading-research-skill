#!/bin/sh
# Bootstrap the skill-owned venv. Idempotent.
# Python 3.13 pinned: the vendored closure + pins are validated on 3.13.
set -e
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3.13}"
command -v "$PY" >/dev/null 2>&1 || { echo "need $PY on PATH (brew install python@3.13)" >&2; exit 1; }
"$PY" -m venv "$SKILL_DIR/.venv"
"$SKILL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$SKILL_DIR/.venv/bin/pip" install --quiet -r "$SKILL_DIR/requirements.txt"
"$SKILL_DIR/.venv/bin/pip" check
echo "venv ready: $SKILL_DIR/.venv ($("$SKILL_DIR/.venv/bin/python" --version))"
