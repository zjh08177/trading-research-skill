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

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import ledger as ledger_mod  # noqa: E402  (replay_path/replay_resolved_path SSOT)

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
    """Coverage/cost-latency date axis: prefer generated_at (when the row was
    actually produced) if present, else fall back to the legacy date_utc / ts /
    run_id-parsed date for rows that predate the generated_at field."""
    if row.get("generated_at"):
        return day(row["generated_at"])
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


def _is_replay_row(row):
    """A usage/ledger row is replay-tainted if explicitly marked as replay
    evidence via mode, evidence_type, or a reports/replay/ report path. The
    default (live-only) corpus must never leak this evidence in."""
    if row.get("mode") == "replay" or row.get("evidence_type") == "replay":
        return True
    rp = row.get("report_path")
    if isinstance(rp, str) and "reports/replay/" in rp:
        return True
    for rp in row.get("report_paths") or []:
        if isinstance(rp, str) and "reports/replay/" in rp:
            return True
    return False


def _usage_by_run_id(usage_rows):
    """First usage event per run_id (evolve's own usage events excluded), used
    to left-join cost/latency/trace metadata onto replay-ledger evidence."""
    out = {}
    for event in usage_rows:
        if event.get("mode") == "evolve":
            continue
        rid = event.get("run_id")
        if not rid or rid in out:
            continue
        out[rid] = event
    return out


def _prefer(usage_val, ledger_val):
    return usage_val if usage_val is not None else ledger_val


def build_index(usage_rows, ledger_rows, runs_dir, before, replay_ledger_ids=None):
    """Live-evidence index. Always excludes replay-tainted usage rows (mode /
    evidence_type / report_path marking replay evidence) so a mixed corpus
    never leaks replay into the live retro. When `replay_ledger_ids` is given
    (only under --include-replay, since that's the only time the replay
    ledger is actually read), a usage row explicitly marked mode="replay"
    with no corresponding replay-ledger run_id is quarantined with a distinct
    warning — a replay call is never inferred from usage alone."""
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
        if _is_replay_row(event):
            rid = event.get("run_id")
            msg = f"excluded replay-tainted usage row from live index: run_id={rid}"
            warnings.append(msg)
            sys.stderr.write(f"WARN: {msg}\n")
            if (replay_ledger_ids is not None and event.get("mode") == "replay"
                    and rid not in replay_ledger_ids):
                qmsg = ("quarantined usage row (mode=replay, no matching replay "
                        f"ledger row): run_id={rid}")
                warnings.append(qmsg)
                sys.stderr.write(f"WARN: {qmsg}\n")
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


def build_replay_index(replay_ledger_rows, usage_by_id, runs_dir, before):
    """Replay-evidence index. Source of truth is the replay ledger itself
    (never usage) — a replay-ledger row with no matching usage still counts
    as replay evidence. Gated on requested_cutoff < before (the historical
    slice being replayed), not generated_at (when it was executed), since
    those can differ arbitrarily for a replay run."""
    warnings = []
    replay_by_id = {}
    dupes = set()
    for row in replay_ledger_rows:
        rid = row.get("run_id")
        if not rid:
            continue
        rc = row.get("requested_cutoff")
        if rc and day(rc) >= before:
            continue
        if rid in replay_by_id:
            dupes.add(rid)
            continue
        replay_by_id[rid] = row
    for rid in sorted(dupes):
        msg = f"duplicate replay run_id: {rid}"
        warnings.append(msg)
        sys.stderr.write(f"WARN: {msg}\n")

    runs = []
    for rid, led in sorted(replay_by_id.items()):
        usage = usage_by_id.get(rid)
        report_path = report_for_run(runs_dir, rid, usage) if usage else report_for_run(runs_dir, rid)
        runs.append({
            "run_id": rid,
            "ticker": led.get("ticker"),
            "requested_cutoff": led.get("requested_cutoff"),
            "generated_at": led.get("generated_at"),
            "host": (usage or {}).get("host", "unknown"),
            "mode": (usage or {}).get("mode") or "replay",
            "status": (usage or {}).get("status") or "success",
            "evidence_type": "replay",
            "join_status": "replay-ledgered" if usage else "replay-ledgered-no-usage",
            "wall_s": _prefer((usage or {}).get("wall_s"), led.get("wall_s")),
            "cost_usd": _prefer((usage or {}).get("cost_usd"), led.get("cost_usd")),
            "report_path": report_path,
            "report_resolved": bool(report_path and Path(report_path).exists()),
            "ledger": led,
        })

    runs.sort(key=lambda r: (r.get("requested_cutoff") or "", r.get("run_id") or ""))
    return {"runs": runs, "warnings": warnings, "ledger_by_id": replay_by_id}


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


def build_signals(index, ledger_path, before, replay_index=None, replay_resolved_path=None):
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
    signals = {
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
    if replay_index is not None:
        # Live/replay split — additive only, never touched by the default
        # (no --include-replay) path, so the base schema stays byte-identical.
        signals["evidence_type_counts"] = {
            "live": len(runs), "replay": len(replay_index["runs"]),
        }
        signals["live_calibration"] = dict(calibration, evidence_type="live")

        replay_resolved = []
        for r in read_jsonl(replay_resolved_path, "replay-resolved-ledger"):
            rd = r.get("resolution_date")
            if rd and day(rd) < before:
                replay_resolved.append(r)
        replay_calibration = (
            {"status": "ready", "n_resolved": len(replay_resolved), "evidence_type": "replay"}
            if replay_resolved else
            {"status": "dormant", "reason": "no resolved outcomes yet",
             "n_resolved": 0, "evidence_type": "replay"}
        )
        signals["replay_calibration"] = replay_calibration
    return signals


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
    if "evidence_type_counts" in signals:
        etc = signals["evidence_type_counts"]
        lines += ["", "## Evidence split (live vs replay)",
                  f"- live n={etc['live']}; replay n={etc['replay']}."]
        live_cal = signals["live_calibration"]
        replay_cal = signals["replay_calibration"]
        live_line = ("dormant — no resolved outcomes yet" if live_cal["status"] == "dormant"
                     else f"n_resolved={live_cal['n_resolved']}")
        replay_line = ("dormant — no resolved outcomes yet" if replay_cal["status"] == "dormant"
                       else f"n_resolved={replay_cal['n_resolved']}")
        lines.append(f"- calibration [evidence_type=live]: {live_line}.")
        lines.append(f"- calibration [evidence_type=replay]: {replay_line}.")
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
    p.add_argument("--include-replay", action="store_true",
                   help="Also index ledger-replay.jsonl as a separate replay "
                        "evidence lane (never merged into the live retro).")
    p.add_argument("--replay-ledger", type=Path, default=None,
                   help="Default: sibling ledger-replay.jsonl of --ledger.")
    p.add_argument("--replay-resolved", type=Path, default=None,
                   help="Default: sibling ledger-replay-resolved.jsonl of --ledger.")
    args = p.parse_args(argv)

    before = day(args.before)
    outdir = args.outdir or (args.runs_dir / f"evolve-{today_stamp()}")
    outdir.mkdir(parents=True, exist_ok=True)

    replay_ledger_path = args.replay_ledger or ledger_mod.replay_path(args.ledger)
    replay_resolved_path = args.replay_resolved or ledger_mod.replay_resolved_path(args.ledger)

    usage_rows = read_jsonl(args.usage_ledger, "usage")
    ledger_rows = read_jsonl(args.ledger, "ledger")

    replay_ledger_rows = read_jsonl(replay_ledger_path, "replay-ledger") if args.include_replay else []
    replay_ledger_ids = ({r["run_id"] for r in replay_ledger_rows if r.get("run_id")}
                         if args.include_replay else None)

    index = build_index(usage_rows, ledger_rows, args.runs_dir, before,
                        replay_ledger_ids=replay_ledger_ids)

    replay_index = None
    if args.include_replay:
        usage_by_id = _usage_by_run_id(usage_rows)
        replay_index = build_replay_index(replay_ledger_rows, usage_by_id, args.runs_dir, before)

    public_index = {
        "before": before.isoformat(),
        "runs": index["runs"],
        "warnings": index["warnings"],
    }
    if replay_index is not None:
        public_index["replay_runs"] = replay_index["runs"]
        public_index["replay_warnings"] = replay_index["warnings"]

    signals = build_signals(index, args.ledger, before, replay_index=replay_index,
                            replay_resolved_path=replay_resolved_path if args.include_replay else None)
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
