#!/usr/bin/env python3
"""Publish the current batch's decision-levels to the vault registry (v2.3 F support).

For every <TICKER>-<asof>-<stamp>/56-levels.json in the skill runs, write
<vault_reports>/levels/<TICKER>.json with ticker+kind+asof added. That directory is the
"live" trigger set monitor_invalidations.py reads; re-running a holding refreshes it.

Usage: publish_levels.py <vault_reports_dir> <asof> [stamp]
"""
import glob
import json
import os
import sys

SK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS = SK + "/runs"
CRYPTO = {'BTC', 'ETH', 'DOGE', 'XRP'}


def main():
    vault_reports = sys.argv[1]
    asof = sys.argv[2]
    stamp = sys.argv[3] if len(sys.argv) > 3 else "1300"
    outdir = os.path.join(vault_reports, "levels")
    os.makedirs(outdir, exist_ok=True)
    n, missing = 0, []
    for f in sorted(glob.glob(f"{RUNS}/*-{asof}-{stamp}/56-levels.json")):
        tk = os.path.basename(os.path.dirname(f)).split('-')[0]
        lv = json.load(open(f))
        if not (lv.get("downside") or lv.get("upside")):
            missing.append(tk)
            continue
        lv = {"ticker": tk, "kind": "crypto" if tk in CRYPTO else "equity", "asof": asof, **lv}
        json.dump(lv, open(os.path.join(outdir, f"{tk}.json"), "w"), indent=1)
        n += 1
    print(json.dumps({"published": n, "dir": outdir, "no_levels": missing}))


if __name__ == "__main__":
    main()
