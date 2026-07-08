#!/usr/bin/env python3
"""Summarize the trading-research usage JSONL."""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def read_jsonl(path):
    rows = []
    if not path.exists():
        return rows
    for i, ln in enumerate(path.read_text().splitlines(), 1):
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            sys.stderr.write(f"WARN: skipped malformed usage line {i} in {path}\n")
    return rows


def summarize(rows, ttl_hours):
    starts = {}
    terminals = {}
    counts = defaultdict(lambda: defaultdict(int))
    now = datetime.now(timezone.utc)
    for row in rows:
        event = row.get("event")
        inv = row.get("invocation_id")
        host = row.get("host", "unknown")
        if event == "start":
            starts[inv] = row
            counts[host]["starts"] += 1
        elif event in {"end", "fail"}:
            terminals[inv] = row
            status = row.get("status") or ("success" if event == "end" else "failed")
            counts[host][status] += 1
        elif event == "host_hook":
            counts[host]["hook_only"] += 1

    ttl = timedelta(hours=ttl_hours)
    for inv, start in starts.items():
        if inv in terminals:
            continue
        ts = _parse_ts(start["ts"])
        if ttl_hours <= 0 or now - ts >= ttl:
            counts[start.get("host", "unknown")]["aborted"] += 1
    return counts


def render(counts):
    lines = ["by host      starts success failed aborted hook_only  rate"]
    for host in sorted(counts):
        c = counts[host]
        starts = c.get("starts", 0)
        success = c.get("success", 0)
        failed = c.get("failed", 0)
        aborted = c.get("aborted", 0)
        hook = c.get("hook_only", 0)
        denom = success + failed + aborted
        rate = f"{(100.0 * success / denom):.1f}%" if denom else "-"
        lines.append(f"{host:<12} {starts:>5} {success:>7} {failed:>6} "
                     f"{aborted:>7} {hook:>9}  {rate}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--ledger", required=True)
    p.add_argument("--ttl-hours", type=float, default=4.0)
    args = p.parse_args(argv)
    rows = read_jsonl(Path(args.ledger))
    print(render(summarize(rows, args.ttl_hours)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
