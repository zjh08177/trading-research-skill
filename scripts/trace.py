#!/usr/bin/env python3
"""trace.py -- profiler query CLI. Reader-only, stdlib-only.

Answers "which step is inefficient, and did my change help" from a run's
`trace/trace.jsonl` (written by pipeline_driver.py's `Trace` class) plus, on
demand, a Codex rollout log. Never writes into a run folder except its own
`trace/summary.json` / `trace/operator.json` caches, never touches
`receipts.json`, `validate_artifact.py`, or `run_stats.py`.

`summarize()` is the one pure function this module owns and
pipeline_driver.py imports (`import trace as trace_mod`): trace events in,
the L2 summary dict out. It is used both by the driver itself (at `finish()`
and on every terminal `write_state()`) and by this CLI's `summary` command
when a run died before writing `summary.json` -- same function either way, so
the two can never disagree.

Commands
  summary <run_dir>                  where did this run's time go
  compare <run_a> <run_b>            did my change help (+ config diff)
  operator <run_dir> [--rollout P]   orchestrator cost around the driver
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import sys
from pathlib import Path

SCHEMA = 1

# Fan-out straggler rule (L2). Duplicated from pipeline_driver.py's constants
# block on purpose: this module must not import the driver (the driver
# imports IT), so the two thresholds are the one intentional bit of drift
# risk in the design -- see design-proposal.md.
STRAGGLER_RATIO = 2.0
STRAGGLER_FLOOR_MS = 60_000


# --- L1 reading --------------------------------------------------------


def read_events(trace_path):
    """Parse `trace/trace.jsonl` into a list of event dicts, in file order.

    Skips unparseable lines (a crash mid-write leaves at most one partial
    trailing line) rather than raising -- criterion 9 requires `summary` to
    still succeed on a trace that stops mid-event."""
    events = []
    p = Path(trace_path)
    if not p.exists():
        return events
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def _parse_ts(t):
    if not t:
        return None
    try:
        return datetime.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except ValueError:
        return None


def _delta_ms(t1, t2):
    a, b = _parse_ts(t1), _parse_ts(t2)
    if a is None or b is None:
        return None
    return int(round((b - a).total_seconds() * 1000))


# --- L2 -- the pure summary function ------------------------------------


def _worker_interval(w):
    """(start, end) monotonic-ish datetimes for a worker-end event, derived
    from its own `t` (completion) and `wall_ms` -- no separate start
    timestamp is stored per worker."""
    end = _parse_ts(w.get("t"))
    wall_ms = w.get("wall_ms")
    if end is None or wall_ms is None:
        return None, None
    return end - datetime.timedelta(milliseconds=wall_ms), end


def _any_concurrent(keepers):
    """True iff at least two of these worker-end rows actually overlapped in
    wall-clock time. Stage 3 (debate) runs 2 workers back-to-back BY DESIGN
    (bear reads the bull's bytes) -- it must not be scored as a fan-out
    straggler just because it has 2 worker-end rows; only stages 2 and 5 are
    real fan-outs (design-proposal.md fact 1)."""
    intervals = [_worker_interval(w) for w in keepers]
    intervals = [(s, e) for s, e in intervals if s is not None]
    for i in range(len(intervals)):
        for j in range(i + 1, len(intervals)):
            s1, e1 = intervals[i]
            s2, e2 = intervals[j]
            if s1 < e2 and s2 < e1:
                return True
    return False


def summarize(events, operator=None):
    """Trace events (a list of parsed trace.jsonl dicts) -> the L2 summary
    dict (schema 1). Pure: no filesystem access, no exceptions on a partial
    or empty event list -- whatever prefix of a crashed run survived is what
    gets summarized (criterion 9). `operator` is the parsed contents of
    `trace/operator.json` when the caller has one on hand, else the L2
    placeholder string is used."""
    run_start = next((e for e in events if e.get("ev") == "run-start"), None)
    run_end = next((e for e in reversed(events) if e.get("ev") == "run-end"), None)

    schema = (run_start or {}).get("schema", SCHEMA)
    driver_version = (run_start or {}).get("driver_version")
    mode = (run_start or {}).get("mode")
    routing_sha256 = (run_start or {}).get("routing_sha256")
    run_id = (run_start or {}).get("run_id")
    ticker = (run_start or {}).get("ticker")

    stage_order = []
    stages = {}
    open_stage = None

    def ensure(stage, name=None):
        if stage not in stages:
            stage_order.append(stage)
            stages[stage] = {
                "stage": stage, "name": name, "status": None, "wall_ms": None,
                "script_ms": 0, "script_n": 0,
                "model_ms_total": 0, "wrapper_ms_total": 0,
                "worker_ends": [],   # every worker-end row seen in this stage
            }
        elif name and not stages[stage]["name"]:
            stages[stage]["name"] = name
        return stages[stage]

    anomalies = []
    prompt_bytes_by_role = {}
    output_bytes_by_role = {}
    # Raw stall episodes, keyed by (role, attempt); raw resumes, keyed the
    # same way; and how many stall episodes the driver already turned into an
    # `anomaly{kind: worker-stalled}`. A stall that resumed within budget is
    # the system working as designed and is NOT an anomaly -- only a stall the
    # run never resumed from (killed/timed out mid-silence) is. A run killed
    # mid-stall never got to emit that anomaly itself -- the leftover is
    # synthesized below so S1 still reaches L2 (criterion 9 + defect 4).
    stall_events = {}
    resume_events = {}
    stall_anomalies = {}
    held_stall_anoms = {}  # driver-emitted worker-stalled anomalies, kept only if unresolved

    for ev in events:
        kind = ev.get("ev")
        if kind == "stage-start":
            info = ensure(ev.get("stage"), ev.get("name"))
            info["status"] = "running"
            open_stage = ev.get("stage")
        elif kind == "stage-end":
            info = ensure(ev.get("stage"), ev.get("name"))
            info["status"] = ev.get("status")
            info["wall_ms"] = ev.get("wall_ms")
            if open_stage == ev.get("stage"):
                open_stage = None
        elif kind == "script":
            info = ensure(open_stage if open_stage is not None else "-")
            info["script_ms"] += ev.get("wall_ms") or 0
            info["script_n"] += 1
        elif kind == "worker-start":
            role = ev.get("role")
            if role is not None and ev.get("prompt_bytes") is not None:
                prompt_bytes_by_role.setdefault(role, ev["prompt_bytes"])
        elif kind == "worker-end":
            # Group by the ENCLOSING stage-start/stage-end window, not the
            # event's own `stage` field: that field is the driver's
            # human-readable per-call label (e.g. "analysts", "judges-n3",
            # "qa-prose") from record_call()'s meta dict, which does not
            # equal the numeric stage codes ("1".."7c") stage-start/stage-end
            # use -- grouping by it directly fragments a real stage into
            # phantom rows with no wall_ms.
            stage = open_stage if open_stage is not None else (ev.get("stage") or "-")
            info = ensure(stage)
            model_ms, wrapper_ms = ev.get("model_ms"), ev.get("wrapper_ms")
            if model_ms is not None:
                info["model_ms_total"] += model_ms
            if wrapper_ms is not None:
                info["wrapper_ms_total"] += wrapper_ms
            info["worker_ends"].append(ev)
            role = ev.get("role")
            if role is not None:
                output_bytes_by_role[role] = ev.get("output_bytes") or 0
        elif kind == "worker-stall":
            stall_events.setdefault((ev.get("role"), ev.get("attempt")), []).append(ev)
        elif kind == "worker-resume":
            resume_events.setdefault((ev.get("role"), ev.get("attempt")), []).append(ev)
        elif kind == "anomaly":
            if ev.get("kind") == "worker-stalled":
                # HOLD it -- a worker-stalled anomaly (whether this driver
                # version emitted one on resume, or an older trace carries one)
                # is kept only if the episode did NOT resume. Deciding that
                # needs the whole event stream, so resolve after the loop.
                key = (ev.get("role"), ev.get("attempt"))
                held_stall_anoms.setdefault(key, []).append(
                    {k: ev.get(k) for k in ("kind", "role", "stage", "ms", "detail")})
                stall_anomalies[key] = stall_anomalies.get(key, 0) + 1
            else:
                anomalies.append({k: ev.get(k) for k in
                                  ("kind", "role", "stage", "ms", "detail")})

    # A stall episode counts as RESOLVED -- no anomaly -- once it has a
    # matching `worker-resume` for the same (role, attempt), or the driver
    # already emitted its own `anomaly{kind: worker-stalled}` for it (the
    # never-resumed/killed-mid-stall case; counted via `stall_anomalies` so
    # that case is never double-counted here). Stalls are sequential per call,
    # so the first N raw `worker-stall` events for a key are covered by its N
    # resumes/already-emitted anomalies; anything past that never resumed
    # before the process died -- the process died still silent. Report it
    # rather than dropping it -- a stall that killed the run is the single most
    # informative row this summary can carry.
    # A driver-emitted worker-stalled anomaly survives only if its episode has
    # no matching resume. A resumed stall is S1 working as designed (a healthy
    # slow call was correctly NOT killed) -- pure noise in L2, and it fires on
    # every run for buffering models (opus/glm) that stream nothing until the
    # end. Dropping it here (not just at the driver) keeps summarize() a correct
    # reader of ANY trace, including ones an older driver wrote.
    for key, held in held_stall_anoms.items():
        if not resume_events.get(key):
            anomalies.extend(held)

    for (role, attempt), evs in stall_events.items():
        covered = (len(resume_events.get((role, attempt), []))
                  + stall_anomalies.get((role, attempt), 0))
        for ev in evs[covered:]:
            age_s = ev.get("age_s") or 0
            anomalies.append({
                "kind": "worker-stalled", "role": role, "stage": ev.get("stage"),
                "ms": int(age_s * 1000),
                "detail": f"no output for {age_s}s (budget left "
                          f"{ev.get('budget_left_s')}s); the trace ends with no "
                          f"resume and no call end — the run died mid-stall",
            })

    # Open (never-closed) stage: the run was killed mid-stage. Report what we
    # can rather than dropping it -- criterion 9's whole point.
    if open_stage is not None and stages[open_stage]["wall_ms"] is None:
        stages[open_stage]["status"] = "interrupted"

    total_wall_ms = (run_end or {}).get("wall_ms")
    closed_wall_sum = sum(s["wall_ms"] for s in stages.values()
                          if isinstance(s.get("wall_ms"), (int, float)))
    interstage_ms = (total_wall_ms - closed_wall_sum
                     if isinstance(total_wall_ms, (int, float)) else None)

    stage_rows = []
    stragglers = []
    waste_rows = []
    for stage in stage_order:
        info = stages[stage]
        wall_ms = info["wall_ms"]
        pct = (round(100.0 * wall_ms / total_wall_ms, 1)
              if wall_ms is not None and total_wall_ms else None)

        # "Keeper" per role: the LAST worker-end chronologically. Everything
        # earlier for that role in this stage was infra-retried or
        # accept-gate-rejected-and-respawned -- discarded, paid-for work.
        by_role = {}
        for w in info["worker_ends"]:
            by_role.setdefault(w.get("role"), []).append(w)
        keepers = [rows[-1] for rows in by_role.values()]
        discarded = [w for rows in by_role.values() for w in rows[:-1]]

        max_worker = max(keepers, key=lambda w: w.get("wall_ms") or 0, default=None)
        model_ms = max_worker.get("model_ms") if max_worker else None
        wrapper_ms = max_worker.get("wrapper_ms") if max_worker else None
        script_ms = info["script_ms"] or None
        driver_ms = None
        if wall_ms is not None:
            known = (model_ms or 0) + (wrapper_ms or 0) + (script_ms or 0)
            driver_ms = wall_ms - known

        stage_rows.append({
            "stage": stage, "name": info["name"], "status": info["status"],
            "wall_ms": wall_ms, "pct_of_driver_wall": pct,
            "model_ms": model_ms, "wrapper_ms": wrapper_ms,
            "script_ms": info["script_ms"], "driver_ms": driver_ms,
            "model_ms_total": info["model_ms_total"] or None,
            "wrapper_ms_total": info["wrapper_ms_total"] or None,
        })

        if len(keepers) > 1 and _any_concurrent(keepers):
            durations = sorted(w.get("wall_ms") or 0 for w in keepers)
            n = len(durations)
            median_ms = durations[n // 2] if n % 2 else \
                (durations[n // 2 - 1] + durations[n // 2]) / 2
            max_ms = durations[-1]
            ratio = (max_ms / median_ms) if median_ms else None
            slowest = max(keepers, key=lambda w: w.get("wall_ms") or 0)
            stragglers.append({
                "stage": stage, "n": n, "median_ms": median_ms, "max_ms": max_ms,
                "straggler_ratio": ratio,
                "slowest_role": slowest.get("role"),
                "slowest_model": slowest.get("cli_model") or slowest.get("model"),
            })
            if ratio is not None and ratio > STRAGGLER_RATIO and \
                    (max_ms - median_ms) > STRAGGLER_FLOOR_MS:
                anomalies.append({
                    "kind": "straggler", "role": slowest.get("role"), "stage": stage,
                    "ms": int(max_ms - median_ms),
                    "detail": f"max/median={ratio:.2f} slowest="
                              f"{slowest.get('role')}/{slowest.get('cli_model') or slowest.get('model')}",
                })

        retries_n = sum(1 for w in info["worker_ends"] if (w.get("attempt") or 1) > 1)
        retries_ms = sum(w.get("wall_ms") or 0 for w in info["worker_ends"]
                         if (w.get("attempt") or 1) > 1)
        timeouts_n = sum(1 for w in info["worker_ends"] if w.get("failure") == "timeout")
        timeouts_ms = sum(w.get("wall_ms") or 0 for w in info["worker_ends"]
                          if w.get("failure") == "timeout")
        discarded_ms = sum(w.get("wall_ms") or 0 for w in discarded)
        if discarded or retries_n or timeouts_n:
            waste_rows.append({
                "stage": stage, "discarded_n": len(discarded), "discarded_ms": discarded_ms,
                "retries_n": retries_n, "retries_ms": retries_ms,
                "timeouts_n": timeouts_n, "timeouts_ms": timeouts_ms,
            })

    stage_rows.sort(key=lambda s: s["wall_ms"] if s["wall_ms"] is not None else -1,
                    reverse=True)

    prompt_ledger = sorted(
        [{"role": role, "prompt_bytes": prompt_bytes_by_role.get(role),
          "output_bytes": output_bytes_by_role.get(role)}
         for role in set(prompt_bytes_by_role) | set(output_bytes_by_role)],
        key=lambda r: r["role"] or "")

    return {
        "schema": schema, "driver_version": driver_version, "mode": mode,
        "routing_sha256": routing_sha256, "run_id": run_id, "ticker": ticker,
        "driver_wall_ms": total_wall_ms, "interstage_ms": interstage_ms,
        "stages": stage_rows, "stragglers": stragglers, "waste": waste_rows,
        "anomalies": anomalies, "prompt_ledger": prompt_ledger,
        "operator": operator if operator is not None
                   else "not extracted — run trace.py operator",
    }


def load_summary(run_dir):
    """`trace/summary.json` if present (recomputing it is NOT this function's
    job -- a hand-edited summary.json, e.g. in a fault-injection test, must be
    read verbatim); otherwise recompute from `trace/trace.jsonl` (criterion 9)."""
    run_dir = Path(run_dir)
    summary_path = run_dir / "trace" / "summary.json"
    if summary_path.exists():
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    operator = None
    op_path = run_dir / "trace" / "operator.json"
    if op_path.exists():
        try:
            operator = json.loads(op_path.read_text(encoding="utf-8"))
        except ValueError:
            operator = None
    events = read_events(run_dir / "trace" / "trace.jsonl")
    return summarize(events, operator=operator)


# --- printing ------------------------------------------------------------


def print_summary(summary, out=sys.stdout):
    run_id = summary.get("run_id") or "?"
    ticker = summary.get("ticker") or "?"
    wall_ms = summary.get("driver_wall_ms")
    wall_s = f"{wall_ms / 1000:.0f}s" if isinstance(wall_ms, (int, float)) else "?"
    print(f"run {run_id}  {ticker}  driver_wall {wall_s}", file=out)
    for st in summary.get("stages", []):
        pct = st.get("pct_of_driver_wall")
        pct_s = f"{pct:.0f}%" if isinstance(pct, (int, float)) else "?%"
        w = st.get("wall_ms")
        w_s = f"{w}ms" if w is not None else "?ms"
        bits = [f"  stage {st.get('stage')} {st.get('name')}", f"{w_s} {pct_s}"]
        if st.get("model_ms") is not None:
            bits.append(f"model {st['model_ms']}ms")
        if st.get("wrapper_ms") is not None:
            bits.append(f"wrapper {st['wrapper_ms']}ms")
        if st.get("script_ms"):
            bits.append(f"script {st['script_ms']}ms")
        if st.get("status") not in (None, "ok"):
            bits.append(f"[{st['status']}]")
        print("  ".join(bits), file=out)
    for s in summary.get("stragglers", []):
        ratio = s.get("straggler_ratio")
        ratio_s = f"{ratio:.2f}" if isinstance(ratio, (int, float)) else "?"
        print(f"  straggler stage {s['stage']}: n={s['n']} median={s['median_ms']}ms "
              f"max={s['max_ms']}ms ratio={ratio_s} "
              f"slowest={s.get('slowest_role')}/{s.get('slowest_model')}", file=out)
    waste = summary.get("waste") or []
    if waste:
        n = sum(w.get("discarded_n") or 0 for w in waste)
        ms = sum(w.get("discarded_ms") or 0 for w in waste)
        print(f"  waste: {n} discarded call(s), {ms}ms", file=out)
    anomalies = summary.get("anomalies") or []
    if anomalies:
        line = " · ".join(
            f"{a.get('kind')}({a.get('role') or a.get('stage')} {a.get('ms')}ms)"
            for a in anomalies)
        print(f"  anomalies: {line}", file=out)
    else:
        print("  anomalies: none", file=out)
    op = summary.get("operator")
    if isinstance(op, dict):
        print(f"  operator: {op.get('model_requests')} requests, "
              f"think {op.get('orchestrator_think_ms')}ms, "
              f"poll {op.get('poll_count')}x/{op.get('poll_ms')}ms", file=out)
    else:
        print(f"  operator: {op}", file=out)


# --- commands --------------------------------------------------------------


def cmd_summary(args):
    summary = load_summary(args.run_dir)
    print_summary(summary)
    return 0


def cmd_compare(args):
    a = load_summary(args.run_a)
    b = load_summary(args.run_b)
    if a.get("schema") != b.get("schema"):
        sys.stderr.write(
            f"WARNING: schema mismatch — deltas may not be like-for-like "
            f"(A schema={a.get('schema')!r} B schema={b.get('schema')!r})\n")

    routing_diff = []
    ra, rb = a.get("routing_sha256"), b.get("routing_sha256")
    if ra != rb:
        routing_diff.append(f"routing_sha256 A={ra} B={rb}")
    print(f"CONFIG: driver_version A={a.get('driver_version')} "
         f"B={b.get('driver_version')} · mode A={a.get('mode')} B={b.get('mode')}"
         + ("".join(f" · {d}" for d in routing_diff)))

    stages_a = {s["stage"]: s for s in a.get("stages", [])}
    stages_b = {s["stage"]: s for s in b.get("stages", [])}
    for stage in sorted(set(stages_a) | set(stages_b),
                        key=lambda s: (stages_b.get(s) or stages_a.get(s) or {})
                        .get("wall_ms") or 0, reverse=True):
        sa, sb = stages_a.get(stage, {}), stages_b.get(stage, {})
        name = sb.get("name") or sa.get("name") or stage
        wa, wb = sa.get("wall_ms"), sb.get("wall_ms")
        if wa is None or wb is None:
            print(f"  {name:14s} A={wa} B={wb} (missing in one run)")
            continue
        delta = wb - wa
        pct = (100.0 * delta / wa) if wa else float("inf")
        print(f"  {name:14s} A={wa}ms B={wb}ms  {delta:+d}ms ({pct:+.1f}%)")

    pa = {p["role"]: p for p in a.get("prompt_ledger", [])}
    pb = {p["role"]: p for p in b.get("prompt_ledger", [])}
    for role in sorted(set(pa) | set(pb)):
        ba = (pa.get(role) or {}).get("prompt_bytes")
        bb = (pb.get(role) or {}).get("prompt_bytes")
        if ba is not None and bb is not None and ba != bb:
            print(f"  prompt-bytes {role}: A={ba} B={bb}  {bb - ba:+d}")

    sa_map = {s["stage"]: s for s in a.get("stragglers", [])}
    sb_map = {s["stage"]: s for s in b.get("stragglers", [])}
    for stage in sorted(set(sa_map) | set(sb_map)):
        ra_, rb_ = sa_map.get(stage), sb_map.get(stage)
        if ra_ and rb_ and ra_.get("straggler_ratio") != rb_.get("straggler_ratio"):
            print(f"  straggler {stage}: A={ra_.get('straggler_ratio')} "
                 f"B={rb_.get('straggler_ratio')}")
    return 0


def cmd_operator(args):
    result, err = extract_operator(args.run_dir, args.rollout)
    if err:
        sys.stderr.write(err + "\n")
        return 3
    run_dir = Path(args.run_dir)
    out_path = run_dir / "trace" / "operator.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}: {result.get('model_requests')} model requests, "
         f"{len(result.get('turns') or [])} turn(s)")
    return 0


def extract_operator(run_dir, rollout_path=None):
    """(operator_dict, None) | (None, error_line). Locates, parses and caches
    orchestrator cost from a Codex rollout log against a run's trace. Never
    guesses: a missing rollout is a loud exit 3, not a fabricated number."""
    run_dir = Path(run_dir)
    events = read_events(run_dir / "trace" / "trace.jsonl")
    run_start = next((e for e in events if e.get("ev") == "run-start"), None)
    run_end = next((e for e in reversed(events) if e.get("ev") == "run-end"), None)
    run_id = (run_start or {}).get("run_id")

    if rollout_path is None:
        home = Path(os.environ.get("HOME", str(Path.home())))
        candidates = sorted(
            glob.glob(str(home / ".codex" / "sessions" / "**" / "rollout-*.jsonl"),
                      recursive=True),
            key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
            reverse=True)[:20]
        found = None
        if run_id:
            needle = run_id.encode("utf-8")
            for c in candidates:
                try:
                    if needle in Path(c).read_bytes():
                        found = c
                        break
                except OSError:
                    continue
        if found is None:
            return None, f"operator: no rollout found for {run_id!r}"
        rollout_path = found

    rp = Path(rollout_path)
    if not rp.exists():
        return None, f"operator: rollout not found: {rp}"

    model_requests = 0
    tokens = None
    unparsed = 0
    open_turns, turns = {}, []
    tool_calls = {}
    tool_durations = []
    first_task_started_t, last_t = None, None

    with rp.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                unparsed += 1
                continue
            ts = d.get("timestamp")
            if ts:
                last_t = ts
            typ = d.get("type")
            payload = d.get("payload") or {}
            if typ == "event_msg":
                ptype = payload.get("type")
                if ptype == "token_count":
                    model_requests += 1
                    usage = (payload.get("info") or {}).get("total_token_usage")
                    if usage:
                        tokens = usage
                elif ptype == "task_started":
                    tid = payload.get("turn_id")
                    open_turns[tid] = ts
                    if first_task_started_t is None:
                        first_task_started_t = ts
                elif ptype == "task_complete":
                    tid = payload.get("turn_id")
                    start_t = open_turns.pop(tid, None)
                    wall_ms = payload.get("duration_ms")
                    if wall_ms is None and start_t and ts:
                        wall_ms = _delta_ms(start_t, ts)
                    turns.append({"turn_id": tid, "wall_ms": wall_ms})
            elif typ == "response_item":
                ptype = payload.get("type")
                if ptype in ("custom_tool_call", "function_call"):
                    call_id = payload.get("call_id")
                    inp = payload.get("input") if ptype == "custom_tool_call" \
                        else payload.get("arguments")
                    tool_calls[call_id] = {"start_t": ts, "input": inp or ""}
                elif ptype in ("custom_tool_call_output", "function_call_output"):
                    call_id = payload.get("call_id")
                    call = tool_calls.get(call_id)
                    if call and call.get("start_t") and ts:
                        ms = _delta_ms(call["start_t"], ts)
                        out = payload.get("output")
                        out_text = out if isinstance(out, str) else json.dumps(out)
                        tool_durations.append({
                            "call_id": call_id, "ms": ms or 0,
                            "input": call.get("input") or "", "output": out_text or "",
                        })

    total_tool_ms = sum(t["ms"] for t in tool_durations)
    total_turn_ms = sum(t["wall_ms"] for t in turns if isinstance(t.get("wall_ms"), (int, float)))
    orchestrator_think_ms = max(0, total_turn_ms - total_tool_ms) if turns else None

    run_dir_str = str(run_dir)
    polls = [t for t in tool_durations
            if run_dir_str in t["input"] or "driver.log" in t["input"]
            or (run_id and run_id in t["input"])]
    poll_count = len(polls)
    poll_ms = sum(t["ms"] for t in polls)
    informative = 0
    prev_output = None
    for t in polls:
        if prev_output is None or t["output"] != prev_output:
            informative += 1
        prev_output = t["output"]
    poll_info_ratio = round(informative / poll_count, 2) if poll_count else None

    pre_driver_ms = (_delta_ms(first_task_started_t, run_start["t"])
                     if run_start and first_task_started_t else None)
    during_driver_ms = (_delta_ms(run_start["t"], run_end["t"])
                        if run_start and run_end else None)
    post_driver_ms = (_delta_ms(run_end["t"], last_t) if run_end and last_t else None)

    rollout_bytes = rp.stat().st_size
    rollout_sha256 = hashlib.sha256(rp.read_bytes()).hexdigest()

    result = {
        "schema": SCHEMA, "run_id": run_id,
        "rollout_path": str(rp), "rollout_sha256": rollout_sha256,
        "rollout_bytes": rollout_bytes, "unparsed_lines": unparsed,
        "model_requests": model_requests,
        "tokens": {
            "input": tokens.get("input_tokens") if tokens else None,
            "cached_input": tokens.get("cached_input_tokens") if tokens else None,
            "output": tokens.get("output_tokens") if tokens else None,
            "reasoning_output": tokens.get("reasoning_output_tokens") if tokens else None,
        } if tokens else {"input": None, "cached_input": None, "output": None,
                          "reasoning_output": None},
        "turns": turns,
        "orchestrator_think_ms": orchestrator_think_ms,
        "tool_ms": total_tool_ms,
        "poll_count": poll_count, "poll_ms": poll_ms,
        "poll_info_ratio": poll_info_ratio,
        "pre_driver_ms": pre_driver_ms, "during_driver_ms": during_driver_ms,
        "post_driver_ms": post_driver_ms,
    }
    return result, None


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="trace.py",
        description="Profiler query CLI (reader-only, stdlib-only).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("summary", help="where did this run's time go")
    s.add_argument("run_dir")
    s.set_defaults(func=cmd_summary)

    c = sub.add_parser("compare", help="did my change help")
    c.add_argument("run_a")
    c.add_argument("run_b")
    c.set_defaults(func=cmd_compare)

    o = sub.add_parser("operator", help="orchestrator cost around the driver")
    o.add_argument("run_dir")
    o.add_argument("--rollout", default=None)
    o.set_defaults(func=cmd_operator)
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
