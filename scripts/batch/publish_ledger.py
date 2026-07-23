#!/usr/bin/env python3
"""Copy a batch's reports to the vault + append one look-ahead-guarded ledger row each.

Pass ONLY the tickers whose ensembles ran THIS batch — never re-append already-ledgered
names (invariant: one ledger row per rating event; re-appending drifts the record).

Usage: publish_ledger.py <asof> <stamp> <T1,T2,...> [wall_total_s] [tokens_total_M] [date_utc]
"""
import json
import os
import re
import shutil
import subprocess
import sys

SK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VAULT = ("/Users/bytedance/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
         "second-brain/Projects/personal/tradingagents")
LEDGER = f"{VAULT}/reports/ledger.jsonl"
NOTCH = ["StrongSell", "Sell", "Hold", "Buy", "StrongBuy"]


def dist_from_votes(run_dir):
    d = {k: 0 for k in NOTCH}
    for i in range(1, 6):
        p = f"{run_dir}/50-votes/vote-{i}.md"
        if not os.path.exists(p):
            continue
        lines = [x for x in open(p).read().splitlines() if x.strip()]
        if lines:
            m = re.search(r"VERDICT:\s*(StrongSell|Sell|Hold|Buy|StrongBuy)", lines[-1])
            if m:
                d[m.group(1)] += 1
    return d


def main():
    asof, stamp, tickers = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
    wall_total = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    tokens_M = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
    date_utc = sys.argv[6] if len(sys.argv) > 6 else f"{asof}T21:00:00Z"
    n = len(tickers)
    wall_per = round(wall_total / n) if n else 0
    cost_per = round(tokens_M * 4.0 / n, 2) if n else 0.0  # ~$4/Mtok blended, amortized

    rows = []
    for t in tickers:
        run_dir = f"{SK}/runs/{t}-{asof}-{stamp}"
        if not os.path.exists(f"{run_dir}/55-decision.json"):
            rows.append({"ticker": t, "rating": "MISSING", "spread": None, "dist": {},
                         "ledger": "skipped — no 55-decision.json", "vault": False})
            continue
        dec = json.load(open(f"{run_dir}/55-decision.json"))
        dist = dist_from_votes(run_dir)
        # Per-name reports live under reports/single-ticker/<TICKER>/ (vault
        # _index.md taxonomy; mirrors the replay publisher's reports/replay/<T>/).
        rel_report = f"reports/single-ticker/{t}/{t}-{asof}.md"
        vault_report = f"{VAULT}/{rel_report}"
        os.makedirs(os.path.dirname(vault_report), exist_ok=True)
        shutil.copyfile(f"{run_dir}/60-report.md", vault_report)
        row = {
            "run_id": f"{t}-{asof}-{stamp}", "ticker": t, "date_utc": date_utc, "as_of": asof,
            "job": "J1-position-aware", "mode_rating": dec.get("mode_label") or dec.get("decision"),
            "distribution": dist, "spread": dec.get("spread"),
            "no_call": dec.get("decision") == "no-call", "gaps": [],
            "report_path": rel_report, "cost_usd": cost_per, "wall_s": wall_per,
        }
        r = subprocess.run(["python3", f"{SK}/scripts/ledger.py", "--ledger", LEDGER,
                            "append", "--row", json.dumps(row)],
                           capture_output=True, text=True)
        rows.append({"ticker": t, "rating": row["mode_rating"], "spread": row["spread"],
                     "dist": dist, "ledger": (r.stdout.strip() or r.stderr.strip()[:80]),
                     "vault": os.path.exists(vault_report)})

    for x in rows:
        print(f"{x['ticker']:5} {str(x['rating']):10} spread={x['spread']} "
              f"dist={x['dist']} vault={x['vault']} | {x['ledger']}")


if __name__ == "__main__":
    main()
