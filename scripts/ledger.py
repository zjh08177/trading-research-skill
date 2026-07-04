#!/usr/bin/env python3
"""Sole ledger entry point: append rows and read with a look-ahead guard.
Path resolves from --ledger, else env TRADING_RESEARCH_LEDGER (no default;
the ~/.tradingagents/... path is BANNED). Read filters rows strictly
date_utc < as_of (same-day excluded); append write-failure prints the row
with a MANUAL-APPEND banner and exits 2.
  ledger.py --ledger <path> append [--row '<json>']   # else reads stdin
  ledger.py --ledger <path> read --ticker X --before <as_of-iso>"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

REQUIRED = ["run_id", "ticker", "date_utc", "as_of", "job", "mode_rating",
            "distribution", "spread", "no_call", "gaps", "report_path",
            "cost_usd", "wall_s"]


def ledger_path(arg):
    p = arg or os.environ.get("TRADING_RESEARCH_LEDGER")
    if not p:
        sys.stderr.write("ERROR: no ledger path (set --ledger or "
                         "TRADING_RESEARCH_LEDGER)\n")
        raise SystemExit(2)
    return Path(p)


def _day(s):
    """Date portion of an ISO date/datetime string, as a date object."""
    return date.fromisoformat(str(s)[:10])


def cmd_append(args):
    raw = args.row if args.row else sys.stdin.read()
    row = json.loads(raw)
    missing = [k for k in REQUIRED if k not in row]
    if missing:
        sys.stderr.write(f"ERROR: row missing keys: {', '.join(missing)}\n")
        raise SystemExit(2)
    line = json.dumps(row, ensure_ascii=False)
    path = ledger_path(args.ledger)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(line + "\n")
    except OSError as e:
        sys.stdout.write("=== MANUAL-APPEND REQUIRED (ledger write failed: "
                         f"{e}) ===\n{line}\n")
        raise SystemExit(2)
    sys.stdout.write(f"appended: {row['run_id']}\n")
    return 0


def cmd_read(args):
    path = ledger_path(args.ledger)
    before = _day(args.before)
    ticker = args.ticker.upper()
    rows = []
    if path.exists():
        for ln in path.read_text().splitlines():
            ln = ln.strip()
            if not ln:
                continue
            r = json.loads(ln)
            if r.get("ticker", "").upper() != ticker:
                continue
            if _day(r["date_utc"]) < before:          # look-ahead guard
                rows.append(r)
    if not rows:
        sys.stdout.write(
            f"No prior track record for {ticker} before {args.before}.\n")
        return 0
    out = ["| Date | Rating | Spread | No-call | Report |",
           "|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: r["date_utc"]):
        out.append(f"| {r['date_utc']} | {r['mode_rating']} | {r['spread']} "
                   f"| {r['no_call']} | {r.get('report_path', '')} |")
    sys.stdout.write("\n".join(out) + "\n")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--ledger")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append")
    a.add_argument("--row")
    r = sub.add_parser("read")
    r.add_argument("--ticker", required=True)
    r.add_argument("--before", required=True)
    args = p.parse_args(argv)
    return cmd_append(args) if args.cmd == "append" else cmd_read(args)


if __name__ == "__main__":
    raise SystemExit(main())
