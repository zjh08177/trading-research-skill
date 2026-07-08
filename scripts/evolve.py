#!/usr/bin/env python3
"""Read-only v2.4a corpus retro for trading-research.

Builds a deterministic index over usage events, ledger rows, and run folders,
then emits coverage-led signals plus a markdown retro. It does not edit the
skill, fetch vendors, append ledger rows, or produce calibration claims.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = (Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"
                  / "second-brain/Projects/personal/tradingagents/reports/ledger.jsonl")
DEFAULT_USAGE = Path.home() / ".local/share/trading-research/usage/invocations.jsonl"
DEFAULT_RUNS = SKILL_DIR / "runs"


def today_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")


def today_day():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def day(s):
    return date.fromisoformat(str(s)[:10])


def read_jsonl(path, label):
    rows = []
    if not path or not path.exists():
        return rows
    for i, ln in enumerate(path.read_text().splitlines(), 1):
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            sys.stderr.write(f"WARN: skipped malformed {label} line {i} in {path}\n")
    return rows


def sidecar_path(main_path):
    return main_path.with_name(main_path.stem + "-resolved" + main_path.suffix)


def report_for_run(runs_dir, run_id, event=None):
    candidates = []
    if event:
        for p in event.get("report_paths") or []:
            candidates.append(Path(p).expanduser())
        if event.get("run_dir"):
            candidates.append(Path(event["run_dir"]).expanduser() / "60-report.md")
    if run_id:
        candidates.append(Path(runs_dir) / run_id / "60-report.md")
    for p in candidates:
        if "reports/evolve/" in str(p):
            continue
        if p.exists():
            return str(p)
    return str(candidates[-1]) if candidates else None


def _event_date(row):
    if row.get("date_utc"):
        return day(row["date_utc"])
    if row.get("ts"):
        return day(row["ts"])
    rid = row.get("run_id")
    if rid:
        parts = rid.split("-")
        if len(parts) >= 4:
            return date.fromisoformat("-".join(parts[-4:-1]))
    return None


def build_index(usage_rows, ledger_rows, runs_dir, before):
    warnings = []
    ledger_by_id = {}
    dupes = set()
    for row in ledger_rows:
        rid = row.get("run_id")
        if not rid:
            continue
        d = _event_date(row)
        if d and d >= before:
            continue
        if rid in ledger_by_id:
            dupes.add(rid)
            continue
        ledger_by_id[rid] = row
    for rid in sorted(dupes):
        msg = f"duplicate run_id: {rid}"
        warnings.append(msg)
        sys.stderr.write(f"WARN: {msg}\n")

    runs = []
    seen = set()
    for event in usage_rows:
        if event.get("mode") == "evolve":
            continue
        d = _event_date(event)
        if d and d >= before:
            continue
        rid = event.get("run_id")
        if event.get("mode") == "batch" and not rid:
            row_id = f"batch:{event.get('invocation_id', 'unknown')}"
            runs.append({
                "run_id": row_id,
                "date_utc": str(d) if d else None,
                "host": event.get("host", "unknown"),
                "mode": "batch",
                "status": event.get("status"),
                "join_status": "batch-parent",
                "child_run_ids": event.get("child_run_ids") or [],
                "report_path": None,
                "report_resolved": False,
            })
            continue
        if not rid or rid in seen:
            continue
        report_path = report_for_run(runs_dir, rid, event)
        led = ledger_by_id.get(rid)
        runs.append({
            "run_id": rid,
            "ticker": event.get("ticker") or (led or {}).get("ticker"),
            "date_utc": str(d) if d else (led or {}).get("date_utc"),
            "host": event.get("host", "unknown"),
            "mode": event.get("mode"),
            "status": event.get("status"),
            "join_status": "ledgered" if led else "unledgered",
            "report_path": report_path,
            "report_resolved": bool(report_path and Path(report_path).exists()),
            **({"ledger": led} if led else {}),
        })
        seen.add(rid)

    for rid, led in sorted(ledger_by_id.items()):
        if rid in seen:
            continue
        report_path = report_for_run(runs_dir, rid)
        runs.append({
            "run_id": rid,
            "ticker": led.get("ticker"),
            "date_utc": led.get("date_utc"),
            "host": "unknown",
            "mode": "report",
            "status": "success",
            "join_status": "ledgered",
            "report_path": report_path,
            "report_resolved": bool(report_path and Path(report_path).exists()),
            "ledger": led,
        })
        seen.add(rid)

    runs.sort(key=lambda r: (r.get("date_utc") or "", r.get("run_id") or ""))
    return {"runs": runs, "warnings": warnings, "ledger_by_id": ledger_by_id}


def _cluster_map(items):
    out = []
    for key, ids in items.items():
        out.append({"key": key, "n": len(set(ids)), "evidence_run_ids": sorted(set(ids))})
    out.sort(key=lambda x: (-x["n"], x["key"]))
    return out


def _numeric_summary(values):
    values = [float(v) for v in values if isinstance(v, (int, float))]
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "max": max(values),
        "avg": sum(values) / len(values),
        "sum": sum(values),
    }


def _cost_latency(runs, ledger_by_id):
    wall, cost = [], []
    evidence = set()
    for row in runs:
        led = row.get("ledger") or ledger_by_id.get(row.get("run_id"), {})
        wall_s = row.get("wall_s", led.get("wall_s"))
        cost_usd = row.get("cost_usd", led.get("cost_usd"))
        if isinstance(wall_s, (int, float)):
            wall.append(wall_s)
            evidence.add(row["run_id"])
        if isinstance(cost_usd, (int, float)):
            cost.append(cost_usd)
            evidence.add(row["run_id"])
    return {
        "n": len(evidence),
        "evidence_run_ids": sorted(evidence),
        "wall_s": _numeric_summary(wall),
        "cost_usd": _numeric_summary(cost),
    }


def build_signals(index, ledger_path, before):
    runs = index["runs"]
    coverage = {
        "total_runs": len(runs),
        "by_host": dict(Counter(r.get("host", "unknown") for r in runs)),
        "by_mode": dict(Counter(r.get("mode", "unknown") for r in runs)),
        "by_status": dict(Counter(r.get("status", "unknown") for r in runs)),
        "by_join_status": dict(Counter(r.get("join_status", "unknown") for r in runs)),
    }
    gaps = defaultdict(list)
    no_call = []
    high_spread = []
    for row in runs:
        led = row.get("ledger") or index["ledger_by_id"].get(row.get("run_id"), {})
        for gap in led.get("gaps") or []:
            gaps[str(gap)].append(row["run_id"])
        if led.get("no_call"):
            no_call.append(row["run_id"])
        if isinstance(led.get("spread"), (int, float)) and led["spread"] >= 2:
            high_spread.append(row["run_id"])

    resolved = []
    side = sidecar_path(ledger_path)
    for r in read_jsonl(side, "resolved-ledger"):
        rd = r.get("resolution_date")
        if rd and day(rd) < before:
            resolved.append(r)
    calibration = {"status": "ready", "n_resolved": len(resolved)} if resolved else {
        "status": "dormant",
        "reason": "no resolved outcomes yet",
        "n_resolved": 0,
    }
    return {
        "coverage": coverage,
        "clusters": {
            "gaps": _cluster_map(gaps),
            "no_call": [{"key": "no_call", "n": len(set(no_call)),
                         "evidence_run_ids": sorted(set(no_call))}] if no_call else [],
            "high_spread": [{"key": "spread>=2", "n": len(set(high_spread)),
                             "evidence_run_ids": sorted(set(high_spread))}] if high_spread else [],
        },
        "cost_latency": _cost_latency(runs, index["ledger_by_id"]),
        "calibration": calibration,
    }


def render_retro(signals, before):
    c = signals["coverage"]
    lines = [f"# Trading-research evolve retro — before {before}", "",
             "## Coverage",
             f"- Total indexed runs: {c['total_runs']}",
             f"- Hosts: {json.dumps(c['by_host'], sort_keys=True)}",
             f"- Modes: {json.dumps(c['by_mode'], sort_keys=True)}",
             f"- Join status: {json.dumps(c['by_join_status'], sort_keys=True)}",
             ""]
    gaps = signals["clusters"]["gaps"]
    lines.append("## Recurring gaps")
    if gaps:
        for g in gaps:
            lines.append(f"- {g['key']} — n={g['n']}; evidence: {', '.join(g['evidence_run_ids'])}")
    else:
        lines.append("- none")
    lines += ["", "## Ensemble / QA signals"]
    for name in ("no_call", "high_spread"):
        cluster = signals["clusters"][name]
        if cluster:
            row = cluster[0]
            lines.append(f"- {row['key']} — n={row['n']}; evidence: {', '.join(row['evidence_run_ids'])}")
    if not signals["clusters"]["no_call"] and not signals["clusters"]["high_spread"]:
        lines.append("- none")
    lines += ["", "## Cost / latency"]
    cl = signals["cost_latency"]
    if cl["n"]:
        wall = cl["wall_s"]
        cost = cl["cost_usd"]
        wall_line = "wall_s none"
        cost_line = "cost_usd none"
        if wall.get("n"):
            wall_line = (f"wall_s max {wall['max']:.0f}, avg {wall['avg']:.1f} "
                         f"(n={wall['n']})")
        if cost.get("n"):
            cost_line = (f"cost_usd sum {cost['sum']:.2f}, avg {cost['avg']:.2f} "
                         f"(n={cost['n']})")
        lines.append(f"- {wall_line}; {cost_line}; evidence: {', '.join(cl['evidence_run_ids'])}")
    else:
        lines.append("- none")
    lines += ["", "## Calibration"]
    cal = signals["calibration"]
    if cal["status"] == "dormant":
        lines.append("- calibration dormant — no resolved outcomes yet.")
    else:
        lines.append(f"- resolved outcome sidecar present: n={cal['n_resolved']}.")
    return "\n".join(lines) + "\n"


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--usage-ledger", type=Path, default=DEFAULT_USAGE)
    p.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    p.add_argument("--outdir", type=Path)
    p.add_argument("--before", default=date.today().isoformat())
    p.add_argument("--min-n", type=int, default=5)
    p.add_argument("--target", default="all")
    p.add_argument("--include-archives", action="store_true")
    p.add_argument("--vault-evolve-dir", type=Path)
    args = p.parse_args(argv)

    before = day(args.before)
    outdir = args.outdir or (args.runs_dir / f"evolve-{today_stamp()}")
    outdir.mkdir(parents=True, exist_ok=True)

    usage_rows = read_jsonl(args.usage_ledger, "usage")
    ledger_rows = read_jsonl(args.ledger, "ledger")
    index = build_index(usage_rows, ledger_rows, args.runs_dir, before)
    public_index = {
        "before": before.isoformat(),
        "runs": index["runs"],
        "warnings": index["warnings"],
    }
    signals = build_signals(index, args.ledger, before)
    write_json(outdir / "10-corpus-index.json", public_index)
    write_json(outdir / "20-signals.json", signals)
    retro = render_retro(signals, before.isoformat())
    (outdir / "30-retro.md").write_text(retro)
    vault_evolve_dir = args.vault_evolve_dir or (args.ledger.parent / "evolve")
    vault_evolve_dir.mkdir(parents=True, exist_ok=True)
    vault_path = vault_evolve_dir / f"evolve-{today_day()}.md"
    vault_path.write_text(retro)
    sys.stdout.write(f"wrote evolve retro: {outdir}\nvault copy: {vault_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
