#!/usr/bin/env python3
"""Deterministic disclosure-footer stats: tally agent count, model mix, and
wall-clock time from the artifacts actually present in a run folder; estimate
token cost from artifact word counts (labeled as an estimate, never claimed as
measured — no per-call token accounting is persisted anywhere in the pipeline).
Stdlib only.

Model mix / agent count come from the run's own delegate RECEIPTS when any are
present (`receipts.json`, or the per-stage `*-receipt.json` / `*-receipts*.json`
files the pipeline writes), so a run with user-overridden routing reports the
models it actually called. Only a receipt-less run falls back to the fixed
per-host preset table.

Usage: run_stats.py <run_dir> [--host claude-code|cursor] [--json]
Exit 0 always (best-effort from whatever artifacts exist); 2 on bad args.
Prints the four Disclosure-footer fields (agent count, model mix, wall clock,
token cost) as a markdown fragment on stdout by default, or the same data as
JSON with --json. This does NOT compute Actual N — that stays ensemble.py's
job; the writer already sources it from 55-rating-block.md."""
import argparse
import datetime
import glob
import json
import os
import re
import sys

# Fixed-preset fallback; receipts are authoritative when present. Used only
# when a run folder carries no receipt files at all (the claude-code host does
# not write them). Model routing IS user-overridable per run, so this table is
# a last resort, never a claim about what a given run actually called.
MODEL_TABLE = {
    "claude-code": {"analyst": "sonnet", "debate": "sonnet", "risk": "sonnet",
                     "prose_qa": "sonnet", "judge": "opus", "writer": "opus"},
    "cursor": {"analyst": "gpt-5.5-medium", "debate": "gpt-5.5-medium",
               "risk": "gpt-5.5-medium", "prose_qa": "gpt-5.5-medium",
               "judge": "opus", "writer": "opus"},
}

# Rough published per-MTok rates (USD), input/output blended down to a single
# output-weighted number since word-count estimation can't separate the two.
# This is explicitly an ESTIMATE input, not a claim of precise pricing. A model
# absent from this table is NEVER silently priced at a default — the estimate
# is withheld and the reason reported instead.
RATE_PER_MTOK_OUT = {"sonnet": 15.0, "opus": 75.0, "gpt-5.5-medium": 10.0}
WORDS_PER_TOKEN = 0.75  # ~4 chars/token, ~5.3 chars/word -> rough word->token

# Receipt bookkeeping ---------------------------------------------------------
# Matches receipts.json, 20-analyst-receipts.json, 30-bull-receipt.json,
# 50-votes/receipts-n3.json, 70-qa-prose-receipt-attempt2.json — and nothing
# else in a run folder (71-run-stats.json, 55-decision.json, ... do not match).
RECEIPT_FILE_RE = re.compile(r"^(?:.*[-_])?receipts?(?:[-_.][^.]*)?\.json$")
RECEIPT_SUFFIX_RE = re.compile(r"[-_]?receipts?(?:[-_.][^.]*)?\.json$")
# CLIs that bill through a flat subscription, so a per-token estimate is a lie.
SUBSCRIPTION_CLIS = ("cursor-agent",)
DRIVER_STATE = "DRIVER-STATE.json"
DRIVER_START_KEYS = ("driver_started_at", "driver_start", "started_at",
                     "start_ts", "start")
DRIVER_END_KEYS = ("driver_ended_at", "driver_end", "ended_at", "end_ts", "end")


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _read_json(path):
    """Return the parsed JSON at path, or None (never raises) — callers report
    the miss rather than swallowing it."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _find_receipt_files(run_dir):
    """Every receipt JSON in the run folder, recursively, path-sorted."""
    hits = [p for p in glob.glob(os.path.join(run_dir, "**", "*.json"), recursive=True)
            if os.path.isfile(p) and RECEIPT_FILE_RE.match(os.path.basename(p))]
    return sorted(hits)


def _stage_label(path, blob):
    """Stage name for a receipt file: its own 'stage' key when it has one,
    else the filename with the numeric prefix and receipt suffix stripped
    (60-writer-receipt.json -> writer)."""
    if isinstance(blob, dict) and blob.get("stage"):
        return str(blob["stage"])
    base = RECEIPT_SUFFIX_RE.sub("", os.path.basename(path))
    base = re.sub(r"^\d+[-_]", "", base)
    return base or "worker"


def _one_entry(call, source_path, label):
    """Normalize one receipt record. The delegate receipt the record POINTS at
    is authoritative (it carries cliModel/exitCode/timing written by the
    wrapper itself); inline fields are the fallback when that file is gone."""
    receipt_path = call.get("receiptPath") or call.get("receipt")
    ext = _read_json(receipt_path) if isinstance(receipt_path, str) else None
    ext = ext if isinstance(ext, dict) else {}
    role = call.get("role")
    if role is None and "slot" in call:
        role = f"slot-{call['slot']}"

    def pick(ext_key, *inline_keys):
        if ext.get(ext_key) is not None:
            return ext[ext_key]
        for k in inline_keys:
            if call.get(k) is not None:
                return call[k]
        return None

    return {
        "source": source_path,
        "stage": call.get("stage") or label,
        "role": role,
        "model": pick("cliModel", "cliModel", "model") or "unknown",
        "exit_code": pick("exitCode", "exitCode"),
        "receipt_path": receipt_path if isinstance(receipt_path, str) else None,
        "receipt_file_present": bool(ext),
        "started_ms": pick("startedAtMs", "startedAtMs"),
        "duration_ms": pick("durationMs", "durationMs"),
        "cli": pick("cli", "cli"),
    }


def _entries_from_blob(blob, source_path, label):
    """Flatten a receipt file into per-call entries. Handles the three shapes
    the pipeline emits: a bare delegate receipt, a stage wrapper with a
    'calls'/'entries' list (stage-level model/cli inherited by each call), and
    a plain list of either."""
    if isinstance(blob, list):
        out = []
        for item in blob:
            out += _entries_from_blob(item, source_path, label)
        return out
    if not isinstance(blob, dict):
        return []
    calls = blob.get("calls")
    if not isinstance(calls, list):
        calls = blob.get("entries")
    if not isinstance(calls, list):
        return [_one_entry(blob, source_path, label)]
    out = []
    for call in calls:
        merged = dict(call) if isinstance(call, dict) else {}
        for key in ("model", "cliModel", "cli", "stage"):
            if key in blob and merged.get(key) is None:
                merged[key] = blob[key]
        out.append(_one_entry(merged, source_path, label))
    return out


def collect_receipts(run_dir):
    """All receipt-backed worker calls in the run, in chronological pipeline
    order: stages sorted by when the stage started, calls kept in the order
    their stage file declares them (slot 1, 2, 3 — not whichever parallel
    worker happened to start first). Falls back to path order for a run whose
    delegate receipts have been pruned, so the order is always deterministic.
    Returns (entries, unreadable_paths)."""
    groups, unreadable = [], []
    for path in _find_receipt_files(run_dir):
        blob = _read_json(path)
        if blob is None:
            unreadable.append(path)
            continue
        group = _entries_from_blob(blob, path, _stage_label(path, blob))
        if group:
            groups.append(group)
    starts = [min((e["started_ms"] for e in g
                   if isinstance(e["started_ms"], (int, float))), default=None)
              for g in groups]
    order = sorted(range(len(groups)),
                   key=lambda i: (starts[i] is None, starts[i] or 0, i))
    entries = [e for i in order for e in groups[i]]
    return entries, unreadable


def count_agents(run_dir):
    """Count Agent-tool invocations from the artifacts they produced. Never
    counts the orchestrator or deterministic scripts (risk_box.py, ensemble.py,
    render_*.py) — only stages tagged 'Agent tool' in the pipeline table."""
    n = 0
    notes = []

    analyst_briefs = sorted(glob.glob(os.path.join(run_dir, "20-analyst-*.md")))
    n += len(analyst_briefs)
    notes.append(f"{len(analyst_briefs)} analyst(s)")

    debate_path = os.path.join(run_dir, "30-debate.md")
    if os.path.exists(debate_path):
        text = open(debate_path).read()
        bull = 1 if re.search(r"##\s*Bull case\b", text, re.I) else 0
        bear = 1 if re.search(r"##\s*Bear case\b", text, re.I) else 0
        n += bull + bear
        notes.append(f"{bull + bear} debate")

    risk_path = os.path.join(run_dir, "40-risk.md")
    if os.path.exists(risk_path):
        n += 1
        notes.append("1 risk officer")

    votes = sorted(glob.glob(os.path.join(run_dir, "50-votes", "vote-*.md")))
    n += len(votes)
    notes.append(f"{len(votes)} judge(s)")

    report_path = os.path.join(run_dir, "60-report.md")
    if os.path.exists(report_path):
        n += 1
        notes.append("1 writer")

    prose_path = os.path.join(run_dir, "70-qa-prose.txt")
    if os.path.exists(prose_path) and open(prose_path).read().strip():
        n += 1
        notes.append("1 QA prose pass")

    return n, "; ".join(notes), len(votes)


def wall_clock_seconds(run_dir):
    """Earliest-to-latest mtime across every artifact in the run folder —
    a real, filesystem-derived measurement, not an estimate."""
    times = []
    for path in glob.glob(os.path.join(run_dir, "**", "*"), recursive=True):
        if os.path.isfile(path):
            t = _mtime(path)
            if t is not None:
                times.append(t)
    if len(times) < 2:
        return None
    return round(max(times) - min(times), 1)


def _to_epoch(value):
    """Epoch seconds from an epoch-seconds number, an epoch-millis number, or
    an ISO-8601 string. None if it is none of those."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value / 1000.0 if value > 1e11 else float(value)
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(
                value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def wall_clock(run_dir):
    """(seconds, source). DRIVER-STATE.json's recorded driver start/end wins
    when present — it brackets the run itself. Artifact mtimes are the
    fallback and are only as good as the moment they are read (a file written
    into the folder later widens the span), so the source is always reported."""
    state = _read_json(os.path.join(run_dir, DRIVER_STATE))
    if isinstance(state, dict):
        if isinstance(state.get("wall_s"), (int, float)):
            return round(float(state["wall_s"]), 1), f"{DRIVER_STATE} wall_s"
        start = next((_to_epoch(state[k]) for k in DRIVER_START_KEYS if k in state), None)
        end = next((_to_epoch(state[k]) for k in DRIVER_END_KEYS if k in state), None)
        if start is not None and end is not None and end >= start:
            return round(end - start, 1), f"{DRIVER_STATE} start/end"
    seconds = wall_clock_seconds(run_dir)
    if seconds is None:
        return None, "unavailable (fewer than 2 timestamped artifacts)"
    return seconds, "artifact mtimes (min->max, as of this invocation)"


def word_count(path):
    if not os.path.exists(path):
        return 0
    return len(open(path).read().split())


def estimate_cost_usd(run_dir, models):
    """Word-count-based estimate, explicitly not a measurement. Sums output
    words per stage, converts to tokens, prices at that stage's model rate.
    Returns (value, note); value is None with a naming note when any stage ran
    a model that has no rate on file — guessing a default price would be a
    silent fabrication."""
    stage_words = {
        "analyst": sum(word_count(p) for p in glob.glob(os.path.join(run_dir, "20-analyst-*.md"))),
        "debate": word_count(os.path.join(run_dir, "30-debate.md")),
        "risk": word_count(os.path.join(run_dir, "40-risk.md")),
        "judge": sum(word_count(p) for p in glob.glob(os.path.join(run_dir, "50-votes", "vote-*.md"))),
        "writer": word_count(os.path.join(run_dir, "60-report.md")),
        "prose_qa": word_count(os.path.join(run_dir, "70-qa-prose.txt")),
    }
    unpriced = sorted({models[stage] for stage, words in stage_words.items()
                       if words and models[stage] not in RATE_PER_MTOK_OUT})
    if unpriced:
        return None, ("not estimated — no published per-MTok rate on file for: "
                      + ", ".join(unpriced))
    total = 0.0
    for stage, words in stage_words.items():
        if words == 0:
            continue
        tokens = words / WORDS_PER_TOKEN
        total += (tokens / 1_000_000) * RATE_PER_MTOK_OUT[models[stage]]
    return round(total, 3), ("estimated from output word counts, not measured "
                             "token usage")


def _build_from_receipts(entries):
    """Footer fields from the run's own delegate receipts — what was actually
    called, not what the preset table says should have been."""
    def failed(e):
        return isinstance(e["exit_code"], int) and e["exit_code"] != 0

    quarantined = [e for e in entries if failed(e)]
    # A receipt entry with no readable exit code still proves the call was
    # made; it is counted, but flagged rather than silently passed off as 0.
    counted = [e for e in entries if not failed(e)]
    unverified = [e for e in counted if e["exit_code"] != 0]

    model_counts = {}
    stage_counts = {}
    for e in counted:
        model_counts[e["model"]] = model_counts.get(e["model"], 0) + 1
        stage_counts[e["stage"]] = stage_counts.get(e["stage"], 0) + 1

    notes = "; ".join(f"{n} {stage}" for stage, n in stage_counts.items())
    if quarantined:
        notes += (f"; {len(quarantined)} quarantined (non-zero exit: "
                  + ", ".join(sorted({str(e['stage']) for e in quarantined})) + ")")
    if unverified:
        notes += (f"; {len(unverified)} with unverified exit status "
                  "(delegate receipt file no longer on disk)")

    subscription = any(e["cli"] in SUBSCRIPTION_CLIS
                       or (e["receipt_path"] or "").find("/cursor-runs/") >= 0
                       for e in counted)
    if subscription:
        cost_usd, cost_note = "cursor-subscription", "N/A"
    else:
        cost_usd, cost_note = None, ("not estimated — receipt-backed runs record "
                                     "no per-call token usage to price")

    return {
        "agent_count": len(counted),
        "agent_notes": notes,
        "model_mix": " + ".join(f"{n}x {m}" for m, n in model_counts.items()),
        "model_counts": model_counts,
        "receipt_paths": [e["receipt_path"] for e in counted if e["receipt_path"]],
        "quarantined": [{"stage": e["stage"], "role": e["role"],
                         "model": e["model"], "exit_code": e["exit_code"]}
                        for e in quarantined],
        "cost_usd": cost_usd if cost_usd is not None else "not estimated",
        "cost_usd_note": cost_note,
        "source": "receipts",
    }


def _build_from_preset(run_dir, host):
    """No receipts in the folder — fall back to the fixed per-host preset."""
    models = MODEL_TABLE.get(host, MODEL_TABLE["claude-code"])
    agent_count, agent_notes, n_judges = count_agents(run_dir)
    cost_usd, cost_note = estimate_cost_usd(run_dir, models)
    return {
        "agent_count": agent_count,
        "agent_notes": agent_notes,
        "model_mix": (f"{n_judges}x {models['judge']} (judges) + 1x "
                      f"{models['writer']} (writer) + {models['analyst']} "
                      f"(analysts/debate/risk/QA-prose)"),
        "cost_usd": cost_usd if cost_usd is not None else "not estimated",
        "cost_usd_note": cost_note,
        "source": f"fixed-preset table (host={host}); no receipts in run folder",
    }


def build(run_dir, host="claude-code"):
    entries, unreadable = collect_receipts(run_dir)
    stats = (_build_from_receipts(entries) if entries
             else _build_from_preset(run_dir, host))
    wall_s, wall_source = wall_clock(run_dir)
    stats["wall_s"] = (wall_s if wall_s is not None
                       else "not recorded (fewer than 2 timestamped artifacts)")
    stats["wall_s_source"] = wall_source
    if unreadable:
        stats["receipt_files_unreadable"] = unreadable
        sys.stderr.write("run_stats.py: WARNING — unparseable receipt file(s), "
                         "excluded from the census: "
                         + ", ".join(unreadable) + "\n")
    return stats


def patch_report(report_path, stats):
    """Replace the writer's literal {{agent_count}}/{{model_mix}}/{{wall_s}}/
    {{cost_usd}} placeholders in the Disclosure section with computed values
    (Stage 7c, runs after both QA checks pass, before Stage 7b HTML render).
    The writer never guesses these four fields — leaves them as literal
    unfilled tokens per the writer card; this is the only thing that fills
    them. {{n_valid}} is untouched — that's ensemble.py's rating-block, not
    ours. Returns True if all four tokens were found and replaced."""
    with open(report_path) as f:
        text = f.read()
    wall = stats["wall_s"]
    wall_str = f"{wall}s" if isinstance(wall, (int, float)) else wall
    cost_str = f"{stats['cost_usd']} ({stats['cost_usd_note']})"
    replacements = {
        "{{agent_count}}": str(stats["agent_count"]),
        "{{model_mix}}": stats["model_mix"],
        "{{wall_s}}s": wall_str,  # template carries the trailing "s" adjacent to the token
        "{{cost_usd}}": cost_str,
    }
    found_all = all(token in text for token in replacements)
    if not isinstance(stats["cost_usd"], (int, float)):
        # Non-numeric cost (flat subscription, or withheld estimate): also eat
        # the template's leading "$" so the footer never reads
        # "$cursor-subscription". Numeric costs keep it.
        text = text.replace("${{cost_usd}}", cost_str)
    for token, value in replacements.items():
        text = text.replace(token, value)
    with open(report_path, "w") as f:
        f.write(text)
    return found_all


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("run_dir")
    p.add_argument("--host", default="claude-code", choices=sorted(MODEL_TABLE))
    p.add_argument("--json", action="store_true")
    p.add_argument("--patch", metavar="REPORT_MD",
                    help="patch {{agent_count}}/{{model_mix}}/{{wall_s}}/{{cost_usd}} "
                         "placeholders in this report file in place")
    args = p.parse_args(argv)

    if not os.path.isdir(args.run_dir):
        sys.stderr.write(f"run_stats.py: no such run dir: {args.run_dir}\n")
        return 2

    stats = build(args.run_dir, args.host)

    if args.patch:
        if not os.path.exists(args.patch):
            sys.stderr.write(f"run_stats.py: --patch target not found: {args.patch}\n")
            return 2
        found_all = patch_report(args.patch, stats)
        if not found_all:
            sys.stderr.write(
                "run_stats.py: WARNING — not all 4 disclosure placeholders were "
                "found in the report (writer may have hand-filled or altered "
                "them); qa_check.py's footer-completeness check is authoritative.\n")
        return 0

    if args.json:
        sys.stdout.write(json.dumps(stats, indent=2) + "\n")
    else:
        wall = stats["wall_s"]
        wall_str = f"{wall}s" if isinstance(wall, (int, float)) else wall
        cost = stats["cost_usd"]
        cost_str = (f"~${cost}" if isinstance(cost, (int, float)) else str(cost))
        sys.stdout.write(
            f"Agents: {stats['agent_count']} ({stats['agent_notes']}) · "
            f"Models: {stats['model_mix']} · Wall clock: {wall_str} · "
            f"Token cost: {cost_str} ({stats['cost_usd_note']}).\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
