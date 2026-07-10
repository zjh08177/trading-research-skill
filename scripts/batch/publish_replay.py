#!/usr/bin/env python3
"""Idempotent publisher for the historical as-of REPLAY lane.

Given a run_dir produced by build_datapack.py --replay (00-scope.json,
10-datapack.md/json, 60-report.md/[.html], 55-decision.json, 50-votes/), this:

  1. loads/creates <run_dir>/75-publish-state.json (side-effect tracking)
  2. copies the frozen 60-report.md(+.html) to
     reports/replay/<TICKER>/<TICKER>-<requested_cutoff>-<stamp>.md/html
     — skipped on a re-run if the source hash already matches
  3. assembles a replay ledger row (evidence_type="replay") and appends it
     via `ledger.py append --replay` — a byte-identical duplicate run_id is a
     no-op success; a conflicting run_id is a hard failure (ledger.py exits 2)
  4. resolves horizons 1/5/21 trading days, but ONLY those already aged
     (resolution_date <= --today); unaged horizons are skipped and become
     resolvable on a later run. Dedup is by ledger.py's resolved_key.
  5. writes <run_dir>/80-replay-eval.md/json summarizing newly + previously
     resolved rows for this run_id (never rewrites 60-report.md)
  6. records every completed side effect back into 75-publish-state.json

Mechanically separate from scripts/batch/publish_ledger.py (the LIVE
publisher): this script NEVER writes reports/single-ticker/ and NEVER
touches the live ledger.jsonl — only the "-replay" sidecar files that
ledger.py's replay_path()/replay_resolved_path() derive from --ledger.

Usage: publish_replay.py <run_dir> [--reports-root DIR] [--ledger PATH]
                          [--wall-s N] [--cost-usd N] [--today YYYY-MM-DD]
                          [--horizons 1,5,21]
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys

SK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(SK, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(SK, "scripts"))
if os.path.join(SK, "scripts", "batch") not in sys.path:
    sys.path.insert(0, os.path.join(SK, "scripts", "batch"))
import ledger as ledger_mod  # noqa: E402
from publish_ledger import dist_from_votes  # noqa: E402

VAULT = ("/Users/bytedance/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
         "second-brain/Projects/personal/tradingagents")
DEFAULT_LEDGER = f"{VAULT}/reports/ledger.jsonl"
DEFAULT_REPORTS_ROOT = VAULT
DEFAULT_HORIZONS = [1, 5, 21]
LEDGER_PY = os.path.join(SK, "scripts", "ledger.py")


def _sha256(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _state_path(run_dir):
    return os.path.join(run_dir, "75-publish-state.json")


def load_state(run_dir):
    p = _state_path(run_dir)
    if os.path.exists(p):
        return json.load(open(p))
    return {}


def save_state(run_dir, state):
    with open(_state_path(run_dir), "w") as f:
        json.dump(state, f, indent=1)
        f.write("\n")


def extract_gaps(datapack_md_text):
    """Pull the bullet list under '## Data gaps' out of 10-datapack.md, so
    replay publish never erases named point-in-time gaps (guardrail #5)."""
    gaps = []
    in_section = False
    for line in datapack_md_text.splitlines():
        if re.match(r"^##\s+Data gaps\s*$", line, re.IGNORECASE):
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            stripped = line.strip()
            if stripped.startswith(("-", "*")):
                gaps.append(stripped[1:].strip())
    return gaps


def _load_scope(run_dir):
    p = os.path.join(run_dir, "00-scope.json")
    if not os.path.exists(p):
        raise SystemExit(
            f"ERROR: {p} missing — publish_replay.py requires a replay "
            "run_dir (build_datapack.py --replay)")
    scope = json.load(open(p))
    missing = [k for k in ("ticker", "requested_cutoff", "effective_market_asof",
                            "entry_market_asof", "generated_at")
               if not scope.get(k)]
    if missing:
        raise SystemExit(f"ERROR: 00-scope.json missing keys: {', '.join(missing)}")
    return scope


def _stamp_from_run_id(run_id, ticker_u, requested_cutoff):
    """run_dir is named <TICKER>-<requested_cutoff>-<stamp> (same convention
    as build_datapack.py's live/replay run_id) — recover the trailing stamp."""
    prefix = f"{ticker_u}-{requested_cutoff}-"
    if run_id.startswith(prefix):
        return run_id[len(prefix):]
    return run_id  # fallback: whole run_id (still unique, just less tidy)


def _decision_fields(run_dir):
    """mode_rating/spread/no_call/judge_mix from 55-decision.json + the
    distribution from votes — the same sources publish_ledger.py's live path
    reads, reused via direct import (dist_from_votes) rather than duplicated."""
    dec_path = os.path.join(run_dir, "55-decision.json")
    dec = json.load(open(dec_path)) if os.path.exists(dec_path) else {}
    return {
        "mode_rating": dec.get("mode_label") or dec.get("decision"),
        "spread": dec.get("spread"),
        "no_call": dec.get("decision") == "no-call",
        "distribution": dist_from_votes(run_dir),
        "judge_mix": dec.get("judge_mix") or [],
    }


def copy_report(run_dir, reports_root, ticker_u, requested_cutoff, stamp, state):
    """Copy 60-report.md(+.html) to reports/replay/<TICKER>/... Idempotent via
    a stored source hash — a byte-identical re-run skips the copy entirely."""
    dest_dir = os.path.join(reports_root, "reports", "replay", ticker_u)
    base = f"{ticker_u}-{requested_cutoff}-{stamp}"
    report_state = state.setdefault("report", {})
    rel_paths = {}

    for ext, key in ((".md", "md"), (".html", "html")):
        src = os.path.join(run_dir, f"60-report{ext}")
        if not os.path.exists(src):
            continue
        digest = _sha256(src)
        dest = os.path.join(dest_dir, base + ext)
        rel = f"reports/replay/{ticker_u}/{base}{ext}"
        prior = report_state.get(key, {})
        if prior.get("hash") == digest and os.path.exists(dest):
            rel_paths[key] = prior.get("path", rel)
            continue
        os.makedirs(dest_dir, exist_ok=True)
        with open(src, "rb") as fsrc, open(dest, "wb") as fdst:
            fdst.write(fsrc.read())
        report_state[key] = {"hash": digest, "path": rel}
        rel_paths[key] = rel
    return rel_paths


def append_ledger_row(row, ledger_path, state):
    """Append via `ledger.py append --replay`. A conflicting run_id is a hard
    failure (ledger.py exits 2, surfaced here — never swallowed); a
    byte-identical duplicate is a no-op success."""
    if state.get("ledger_appended") and state.get("ledger_row") == row:
        return "already-appended (state)"
    r = subprocess.run(
        [sys.executable, LEDGER_PY, "--ledger", ledger_path, "append", "--replay",
         "--row", json.dumps(row)],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(
            f"FAIL: ledger append --replay failed for {row['run_id']} "
            f"(rc={r.returncode}): {r.stderr.strip()}")
    state["ledger_appended"] = True
    state["ledger_row"] = row
    return r.stdout.strip()


def resolve_aged_horizons(ticker_u, entry_market_asof, ledger_path, today,
                           horizons, state):
    """Resolve only horizons whose resolution_date <= today (further deduped
    by ledger.py's resolved_key so re-runs never duplicate a resolved row).
    Unaged horizons are skipped now and simply resolvable on a later run —
    this is the ONLY place a real (network) price fetch can happen, and only
    once a horizon has actually settled."""
    entry_date = dt.date.fromisoformat(str(entry_market_asof)[:10])
    resolved_state = state.setdefault("resolved_horizons", [])
    called, pending = [], []
    for h in horizons:
        rdate = ledger_mod.add_trading_days(entry_date, h)
        if rdate > today:
            pending.append(h)
            continue
        r = subprocess.run(
            [sys.executable, LEDGER_PY, "--ledger", ledger_path, "resolve",
             "--replay", "--ticker", ticker_u, "--horizon", str(h),
             "--asof", today.isoformat()],
            capture_output=True, text=True)
        if r.returncode != 0:
            sys.stderr.write(r.stderr)
            raise SystemExit(
                f"FAIL: ledger resolve --replay failed for {ticker_u} "
                f"horizon={h}: {r.stderr.strip()}")
        called.append(h)
        if h not in resolved_state:
            resolved_state.append(h)
    return called, pending


def _resolved_rows_for_run(run_id, ledger_path):
    side = ledger_mod.replay_resolved_path(ledger_mod.ledger_path(ledger_path))
    return [r for r in ledger_mod.read_jsonl(side, "replay-resolved-ledger")
            if r.get("run_id") == run_id]


def write_eval(run_dir, run_id, ticker_u, ledger_path, pending, newly_keys, state):
    """Write 80-replay-eval.md/json summarizing this run's newly + previously
    resolved rows. Generated AFTER the frozen report; never touches it."""
    rows = _resolved_rows_for_run(run_id, ledger_path)
    newly = [r for r in rows if ledger_mod.resolved_key(r) in newly_keys]
    previously = [r for r in rows if ledger_mod.resolved_key(r) not in newly_keys]

    payload = {
        "run_id": run_id, "ticker": ticker_u,
        "newly_resolved": newly, "previously_resolved": previously,
        "pending_horizons": pending,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(os.path.join(run_dir, "80-replay-eval.json"), "w") as f:
        json.dump(payload, f, indent=1)
        f.write("\n")

    lines = [f"# Replay eval — {ticker_u} ({run_id})", ""]

    def _render(rows_):
        for r in rows_:
            lines.append(f"- {r['horizon_td']}td vs {r['benchmark']}: alpha "
                         f"{r['alpha']:+.4f}, hit={r['hit']}")
        lines.append("")

    if newly:
        lines.append("## Newly resolved")
        _render(newly)
    if previously:
        lines.append("## Previously resolved")
        _render(previously)
    if pending:
        lines.append(f"## Pending (unaged): {', '.join(str(h) for h in pending)}td")
        lines.append("")
    if not (newly or previously):
        lines.append("No resolved horizons yet.")
        lines.append("")

    with open(os.path.join(run_dir, "80-replay-eval.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    state["eval_written"] = True
    return payload


def publish_run(run_dir, reports_root=DEFAULT_REPORTS_ROOT, ledger_path=DEFAULT_LEDGER,
                 wall_s=0.0, cost_usd=0.0, today=None, horizons=None):
    run_dir = os.path.abspath(run_dir)
    horizons = horizons or DEFAULT_HORIZONS
    if today is None:
        today = dt.date.today()
    elif isinstance(today, str):
        today = dt.date.fromisoformat(today)

    scope = _load_scope(run_dir)
    ticker = scope["ticker"]
    ticker_u = str(ticker).upper()
    requested_cutoff = scope["requested_cutoff"]
    run_id = os.path.basename(run_dir.rstrip("/"))
    stamp = _stamp_from_run_id(run_id, ticker_u, requested_cutoff)

    state = load_state(run_dir)
    if state.get("run_id") and state["run_id"] != run_id:
        raise SystemExit(
            f"FAIL: 75-publish-state.json belongs to run_id {state['run_id']!r}, "
            f"not this run_dir's {run_id!r}")
    state["run_id"] = run_id

    rel_paths = copy_report(run_dir, reports_root, ticker_u, requested_cutoff, stamp, state)
    save_state(run_dir, state)  # checkpoint: report copy done even if ledger append fails

    datapack_md = os.path.join(run_dir, "10-datapack.md")
    gaps = extract_gaps(open(datapack_md).read()) if os.path.exists(datapack_md) else []
    dec_fields = _decision_fields(run_dir)

    row = {
        "run_id": run_id, "ticker": ticker,
        "generated_at": scope["generated_at"],
        "requested_cutoff": requested_cutoff,
        "effective_market_asof": scope["effective_market_asof"],
        "entry_market_asof": scope["entry_market_asof"],
        "job": scope.get("job", "J1-replay"),
        "mode_rating": dec_fields["mode_rating"],
        "distribution": dec_fields["distribution"],
        "spread": dec_fields["spread"],
        "no_call": dec_fields["no_call"],
        "gaps": gaps,
        "judge_mix": dec_fields["judge_mix"],
        "report_path": rel_paths.get("md", ""),
        "cost_usd": cost_usd, "wall_s": wall_s,
        "evidence_type": "replay",
    }
    if "html" in rel_paths:
        row["report_path_html"] = rel_paths["html"]

    ledger_msg = append_ledger_row(row, ledger_path, state)
    save_state(run_dir, state)

    keys_before = {ledger_mod.resolved_key(r)
                   for r in _resolved_rows_for_run(run_id, ledger_path)}
    called, pending = resolve_aged_horizons(
        ticker_u, scope["entry_market_asof"], ledger_path, today, horizons, state)
    keys_after_new = set()
    if called:
        keys_after_new = {ledger_mod.resolved_key(r)
                          for r in _resolved_rows_for_run(run_id, ledger_path)} - keys_before
    save_state(run_dir, state)

    eval_payload = write_eval(run_dir, run_id, ticker_u, ledger_path, pending,
                              keys_after_new, state)
    save_state(run_dir, state)

    return {"run_id": run_id, "ticker": ticker_u, "report_path": rel_paths.get("md"),
            "ledger_msg": ledger_msg, "resolved_called": called, "pending": pending,
            "eval": eval_payload}


def _build_arg_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir")
    p.add_argument("--reports-root", default=DEFAULT_REPORTS_ROOT)
    p.add_argument("--ledger", default=DEFAULT_LEDGER)
    p.add_argument("--wall-s", type=float, default=0.0)
    p.add_argument("--cost-usd", type=float, default=0.0)
    p.add_argument("--today", default=None)
    p.add_argument("--horizons", default="1,5,21")
    return p


def main(argv=None):
    args = _build_arg_parser().parse_args(argv if argv is not None else sys.argv[1:])
    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    result = publish_run(args.run_dir, reports_root=args.reports_root,
                         ledger_path=args.ledger, wall_s=args.wall_s,
                         cost_usd=args.cost_usd, today=args.today, horizons=horizons)
    sys.stdout.write(json.dumps(result, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
