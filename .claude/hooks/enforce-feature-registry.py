#!/usr/bin/env python3
"""Stop hook: every impl-plan doc in the tradingagents vault program must have a
row in feature-registry.md. Fires when a feature is proposed/started (its
impl-plan lands) without a registry row — the registry tracks proposed →
building → live, not just shipped.

Gate keys on impl-plan-*.md basenames only (registry ref convention). Excluded:
reviews/ archive/ logs/ dirs, dated round-copies (-20xx- in the name), and the
grandfathered pre-registry base docs below.
"""
import json
import re
import sys
from pathlib import Path

PROG = Path.home() / ("Library/Mobile Documents/iCloud~md~obsidian/Documents/"
                      "second-brain/Projects/personal/tradingagents")
REGISTRY = PROG / "feature-registry.md"
EXCLUDE_DIRS = {"reviews", "archive", "logs"}
GRANDFATHERED = {"impl-plan-v2-skillify", "impl-plan-v3-quant-engine"}
DATED = re.compile(r"-20\d\d-")


def main():
    if not REGISTRY.exists():
        return 0  # vault offline/evicted — never block on infra
    reg_text = REGISTRY.read_text(encoding="utf-8", errors="replace")
    missing = []
    for f in PROG.rglob("impl-plan-*.md"):
        if EXCLUDE_DIRS & set(p.name for p in f.parents):
            continue
        base = f.stem
        if base in GRANDFATHERED or DATED.search(base):
            continue
        if base not in reg_text:
            missing.append(base)
    if not missing:
        return 0
    listing = "\n".join("  " + b for b in sorted(set(missing)))
    reason = (
        "STOP: feature doc(s) with no row in the capability registry "
        f"({REGISTRY}):\n\n{listing}\n\n"
        "Add one row per feature to the matching sub-project section — status "
        "proposed (impl-plan exists, build not started), building, or live — "
        "with Ref [[<impl-plan-basename>]]. Then end the turn.")
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
