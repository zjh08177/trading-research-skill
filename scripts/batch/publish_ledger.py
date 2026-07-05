#!/usr/bin/env python3
"""Publish the 10 reports to the vault + append one look-ahead-guarded ledger row each."""
import json
import os
import re
import shutil
import subprocess

SK = "/Users/bytedance/.claude/skills/trading-research"
VAULT = ("/Users/bytedance/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
         "second-brain/Projects/personal/tradingagents")
LEDGER = f"{VAULT}/reports/ledger.jsonl"
ASOF = "2026-07-05"
STAMP = "1300"
DATE_UTC = "2026-07-05T21:05:00Z"
NOTCH = ["StrongSell", "Sell", "Hold", "Buy", "StrongBuy"]
TICKERS = ["TSLA", "AMD", "META", "AAOI", "MSFT", "BTC", "NVDA", "XLE", "NOK", "AMZN"]
# batch: 184 agents, 9.53M subagent tokens, 1398s wall. Amortized per ticker.
WALL_PER = round(1398 / 10)
COST_PER = round(9.53e6 / 1e6 * 4.0 / 10, 2)  # ~blended $4/Mtok, /10 tickers


def dist_from_votes(run_dir):
    d = {k: 0 for k in NOTCH}
    for i in range(1, 6):
        p = f"{run_dir}/50-votes/vote-{i}.md"
        if not os.path.exists(p):
            continue
        m = re.search(r"VERDICT:\s*(StrongSell|Sell|Hold|Buy|StrongBuy)",
                      open(p).read().splitlines()[-1] if open(p).read().strip() else "")
        # last non-empty line
        lines = [x for x in open(p).read().splitlines() if x.strip()]
        if lines:
            m = re.search(r"VERDICT:\s*(StrongSell|Sell|Hold|Buy|StrongBuy)", lines[-1])
            if m:
                d[m.group(1)] += 1
    return d


rows = []
for t in TICKERS:
    run_dir = f"{SK}/runs/{t}-{ASOF}-{STAMP}"
    dec = json.load(open(f"{run_dir}/55-decision.json"))
    dist = dist_from_votes(run_dir)
    vault_report = f"{VAULT}/reports/{t}-{ASOF}.md"
    shutil.copyfile(f"{run_dir}/60-report.md", vault_report)
    row = {
        "run_id": f"{t}-{ASOF}-{STAMP}", "ticker": t, "date_utc": DATE_UTC, "as_of": ASOF,
        "job": "J1-position-aware", "mode_rating": dec.get("mode_label") or dec.get("decision"),
        "distribution": dist, "spread": dec.get("spread"),
        "no_call": dec.get("decision") == "no-call", "gaps": [],
        "report_path": f"reports/{t}-{ASOF}.md", "cost_usd": COST_PER, "wall_s": WALL_PER,
    }
    r = subprocess.run(["python3", f"{SK}/scripts/ledger.py", "--ledger", LEDGER,
                        "append", "--row", json.dumps(row)],
                       capture_output=True, text=True)
    rows.append({"ticker": t, "rating": row["mode_rating"], "spread": row["spread"],
                 "dist": dist, "ledger": r.stdout.strip() or r.stderr.strip()[:80],
                 "vault": os.path.exists(vault_report)})

for x in rows:
    print(f"{x['ticker']:5} {x['rating']:10} spread={x['spread']} "
          f"dist={x['dist']} vault={x['vault']} | {x['ledger']}")
