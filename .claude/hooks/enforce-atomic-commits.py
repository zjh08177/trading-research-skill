#!/usr/bin/env python3
"""Stop hook — enforce ONE COMMIT PER ATOM for the trading-research pipeline.

Blocks turn-end while the skill repo has uncommitted tracked/untracked changes,
so a working task cannot end with dirty, unversioned atoms (the failure this fixes).

Fail-safe by construction: it only ever blocks on a POSITIVE dirty detection.
Any git error, timeout, missing repo, or a loop-guard trip → exit 0 (no block).
Run artifacts (runs/) are .gitignore'd so live account data never triggers it.
"""
import json
import subprocess
import sys

REPO = "/Users/bytedance/.claude/skills/trading-research"


def main():
    # Loop guard: if the hook already fired this turn and we're being re-invoked
    # inside a stop-block, don't re-block (the runtime passes stop_hook_active).
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if payload.get("stop_hook_active"):
        return 0
    try:
        r = subprocess.run(["git", "-C", REPO, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return 0
    if r.returncode != 0:
        return 0
    dirty = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if not dirty:
        return 0
    listing = "\n".join("  " + ln for ln in dirty[:50])
    reason = (
        "STOP: the trading-research skill repo has uncommitted changes. "
        "Discipline: one commit per atom — an atom is not done until it is committed.\n\n"
        f"{listing}\n\n"
        f"Commit each logical change before ending, e.g.:\n"
        f"  git -C {REPO} add <files> && git -C {REPO} commit -m 'type(scope): ...'\n"
        "If a change is deliberate WIP you must leave, commit it as 'wip: ...' or "
        "`git stash` it — do not end the turn with a dirty tree. "
        "(Run artifacts under runs/ are gitignored and never appear here.)")
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
