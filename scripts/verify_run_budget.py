#!/usr/bin/env python3
"""Budget + honesty gate for one `pipeline_driver.py` run. Stdlib only.

Speed and honesty share ONE exit code on purpose: the Codex re-architecture
exists to make a run fast, and the only way that could go wrong is by trading a
gate for the clock. So this script refuses to certify either half alone --
`--max-wall-s` (speed) and the QA re-run (honesty) both feed the same result.

It NEVER trusts a recorded pass. `DRIVER-STATE.json:qa` and `70-qa-final.txt`
are the driver's own testimony about a check it ran itself; this re-runs
`qa_check.py --strict` and `qa_check.py --strict --check-footer` against the
run folder's artifacts as they stand on disk right now. A report edited after
the driver finished therefore fails here even though the run folder still
carries a green `70-qa-final.txt`.

Usage:
  verify_run_budget.py <run_dir> [--max-wall-s S] [--require-qa-strict]
                       [--skip-qa] [--rollout <rollout.jsonl>]
                       [--max-model-requests N] [--json]
                       [--python PATH] [--skill-dir DIR]

Exit 0 every check passed - 1 any check failed - 2 bad invocation.
No network, no vendor call, no model call: every check is offline.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BAD_INVOCATION = 2

STATE_FILE = "DRIVER-STATE.json"
REPORT_FILE = "60-report.md"
DATAPACK_FILE = "10-datapack.json"
POSITION_FILE = "15-position.json"
DEBATE_FILE = "30-debate.md"
PROSE_QA_FILE = "70-qa-prose.txt"
ANALYSTS = ("fund", "tech", "sent", "meanrev")

# Wall-clock keys, in the order DRIVER-STATE.json / older run folders use them.
WALL_KEYS = ("wall_s", "wall_clock_s", "elapsed_s")
START_KEYS = ("driver_started_at", "driver_start", "started_at", "start_ts", "start")
END_KEYS = ("driver_ended_at", "driver_end", "ended_at", "end_ts", "end")

# Codex rollout event kinds that each cost one orchestrator model request.
# `function_call` = a native tool call, `custom_tool_call` = a freeform/custom
# tool call (Codex's shell + apply_patch land here). Both are counted; the
# matching `*_output` records are the tool's reply, not a request, and are not.
MODEL_REQUEST_TYPES = ("function_call", "custom_tool_call")


class Check:
    """One verdict line. `ok=None` means the check did not run and was not
    required -- reported as SKIP, never silently dropped, and never counted as
    a pass."""

    def __init__(self, name, ok, detail, extra=None):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.extra = extra or {}

    @property
    def label(self):
        return "PASS" if self.ok else ("SKIP" if self.ok is None else "FAIL")

    def as_dict(self):
        # extras first: the four reserved keys always win, so a check-specific
        # field can never quietly redefine this check's own verdict.
        return {**self.extra, "check": self.name, "status": self.label.lower(),
                "ok": self.ok, "detail": self.detail}


# --- DRIVER-STATE / wall clock ----------------------------------------------

def load_state(run_dir):
    """Returns (state_dict_or_None, Check). A run with no readable state cannot
    prove its own wall clock, so that is a failure, not a skip."""
    path = run_dir / STATE_FILE
    if not path.exists():
        return None, Check("driver-state", False,
                           f"{STATE_FILE} missing -- this run folder was not "
                           f"produced by pipeline_driver.py, or the driver died "
                           f"before finish()")
    try:
        with open(path) as f:
            state = json.load(f)
    except (ValueError, OSError) as e:
        return None, Check("driver-state", False, f"{STATE_FILE} unreadable: {e}")
    if not isinstance(state, dict):
        return None, Check("driver-state", False,
                           f"{STATE_FILE} is not a JSON object")
    bits = [f"status={state.get('status')!r}",
            f"exit_code={state.get('exit_code')}"]
    if state.get("mode"):
        bits.append(f"mode={state['mode']!r}")
    if state.get("quarantines"):
        bits.append(f"quarantines={len(state['quarantines'])}")
    return state, Check("driver-state", True, ", ".join(bits),
                        {"driver_status": state.get("status"),
                         "driver_exit_code": state.get("exit_code"),
                         "mode": state.get("mode")})


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def wall_clock(state):
    """(seconds, source) from DRIVER-STATE.json, or (None, reason). Prefers the
    driver's own recorded figure; falls back to its start/end stamps. Artifact
    mtimes are deliberately NOT a fallback here -- run_stats.py may use them for
    a footer estimate, but a budget gate must not certify a guess."""
    if state is None:
        return None, f"{STATE_FILE} unavailable"
    for key in WALL_KEYS:
        value = state.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value), f"{STATE_FILE}:{key}"
    start = next((_parse_ts(state.get(k)) for k in START_KEYS
                  if _parse_ts(state.get(k))), None)
    end = next((_parse_ts(state.get(k)) for k in END_KEYS
                if _parse_ts(state.get(k))), None)
    if start and end:
        return (end - start).total_seconds(), f"{STATE_FILE}:start/end stamps"
    return None, (f"{STATE_FILE} records no wall clock "
                  f"({'/'.join(WALL_KEYS)} absent and start/end unparseable)")


def check_wall(state, max_wall_s):
    seconds, source = wall_clock(state)
    if seconds is None:
        if max_wall_s is None:
            return Check("wall-clock", None, f"not checked: {source}")
        return Check("wall-clock", False,
                     f"--max-wall-s {max_wall_s:g} cannot be enforced: {source}")
    extra = {"wall_s": round(seconds, 2), "wall_s_source": source,
             "max_wall_s": max_wall_s}
    if max_wall_s is None:
        return Check("wall-clock", None,
                     f"{seconds:.1f}s (source {source}); no --max-wall-s given",
                     extra)
    if seconds > max_wall_s:
        return Check("wall-clock", False,
                     f"{seconds:.1f}s exceeds the {max_wall_s:g}s budget by "
                     f"{seconds - max_wall_s:.1f}s (source {source})", extra)
    return Check("wall-clock", True,
                 f"{seconds:.1f}s within the {max_wall_s:g}s budget "
                 f"(source {source})", extra)


# --- QA re-run ---------------------------------------------------------------

def qa_flags(run_dir, footer):
    """Mirror pipeline_driver._qa_flags: the same artifacts the driver gated on,
    read fresh off disk. Optional inputs are passed only when present -- a
    quarantined analyst brief has no accepted file to re-scan, and its data gap
    is already disclosed in the report."""
    args = [str(run_dir / REPORT_FILE), str(run_dir / DATAPACK_FILE)]
    if (run_dir / POSITION_FILE).exists():
        args.append(str(run_dir / POSITION_FILE))
    args.append("--strict")
    if (run_dir / DEBATE_FILE).exists():
        args += ["--debate", str(run_dir / DEBATE_FILE)]
    for role in ANALYSTS:
        brief = run_dir / f"20-analyst-{role}.md"
        if brief.exists():
            args += ["--brief", str(brief)]
    if (run_dir / PROSE_QA_FILE).exists():
        args += ["--prose-qa", str(run_dir / PROSE_QA_FILE)]
    if footer:
        args.append("--check-footer")
    return args


def _failing_lines(stdout, limit=12):
    lines = [ln for ln in stdout.splitlines() if ln.startswith("! ")]
    if len(lines) > limit:
        lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
    return lines


def run_qa(run_dir, python, skill_dir, footer, name):
    script = Path(skill_dir) / "scripts" / "qa_check.py"
    if not script.exists():
        return Check(name, False, f"qa_check.py not found at {script}")
    cmd = [str(python), str(script)] + qa_flags(run_dir, footer)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    flagstr = " ".join(a if not a.startswith(str(run_dir)) else os.path.basename(a)
                       for a in cmd[2:])
    tail = next((ln for ln in reversed(proc.stdout.splitlines())
                 if ln.startswith("== ") and " pass, " in ln), "").strip("= ")
    extra = {"exit_code": proc.returncode, "flags": flagstr,
             "summary": tail, "failures": _failing_lines(proc.stdout)}
    if proc.returncode == 0:
        return Check(name, True, f"exit 0 ({tail})" if tail else "exit 0", extra)
    detail = [f"exit {proc.returncode}" + (f" ({tail})" if tail else "")]
    detail += ["    " + ln for ln in extra["failures"]]
    if proc.returncode not in (0, 1) and proc.stderr.strip():
        detail.append("    stderr: " + proc.stderr.strip().splitlines()[0])
    return Check(name, False, "\n".join(detail), extra)


def check_qa(run_dir, python, skill_dir, require, skip):
    """Two re-runs: the cite/gap/duplication gate, then the same gate plus the
    invariant-7 disclosure-footer check. Split so a failure is attributable --
    a stale footer is a different defect from a mis-cited number."""
    if skip:
        c = Check("qa-rerun", None, "--skip-qa given: the QA gates were NOT "
                                    "re-verified by this run")
        return [c]
    missing = [f for f in (REPORT_FILE, DATAPACK_FILE) if not (run_dir / f).exists()]
    if missing:
        detail = (f"cannot re-run qa_check.py: {', '.join(missing)} absent from "
                  f"{run_dir}")
        if require:
            return [Check("qa-rerun", False,
                          detail + " -- --require-qa-strict makes an unverifiable "
                                   "run a failure, so an absent report can never "
                                   "pass by default")]
        return [Check("qa-rerun", None, detail)]
    return [run_qa(run_dir, python, skill_dir, False, "qa-strict"),
            run_qa(run_dir, python, skill_dir, True, "qa-check-footer")]


# --- rollout accounting ------------------------------------------------------

def count_model_requests(path):
    """(count, stats) over a Codex rollout JSONL, offline. Handles both the
    wrapped `{"type": "response_item", "payload": {"type": ...}}` shape and a
    flat `{"type": ...}` record. Unparseable lines are counted and reported --
    never skipped in silence."""
    count = 0
    by_type = {}
    lines = bad = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            lines += 1
            try:
                obj = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if not isinstance(obj, dict):
                bad += 1
                continue
            payload = obj.get("payload")
            kind = (payload.get("type") if isinstance(payload, dict) else None) \
                or obj.get("type")
            if kind in MODEL_REQUEST_TYPES:
                count += 1
                by_type[kind] = by_type.get(kind, 0) + 1
    return count, {"rollout_lines": lines, "rollout_unparseable_lines": bad,
                   "by_type": by_type}


def check_rollout(rollout, max_requests):
    if rollout is None:
        if max_requests is not None:
            return Check("model-requests", False,
                         "--max-model-requests given without --rollout: there is "
                         "nothing to count")
        return None
    path = Path(rollout)
    if not path.exists():
        return Check("model-requests", False, f"rollout not found: {path}")
    try:
        count, stats = count_model_requests(path)
    except OSError as e:
        return Check("model-requests", False, f"rollout unreadable: {e}")
    mix = ", ".join(f"{k}={v}" for k, v in sorted(stats["by_type"].items())) or "none"
    extra = {"model_requests": count, "max_model_requests": max_requests, **stats}
    note = ""
    if stats["rollout_unparseable_lines"]:
        note = (f"; WARNING {stats['rollout_unparseable_lines']} unparseable "
                f"line(s) were skipped, so the count is a LOWER BOUND")
    if max_requests is None:
        return Check("model-requests", None,
                     f"{count} orchestrator model requests ({mix}); no "
                     f"--max-model-requests given{note}", extra)
    if count > max_requests:
        return Check("model-requests", False,
                     f"{count} orchestrator model requests exceeds the "
                     f"{max_requests} budget ({mix}){note}", extra)
    return Check("model-requests", True,
                 f"{count} orchestrator model requests within the "
                 f"{max_requests} budget ({mix}){note}", extra)


# --- driver ------------------------------------------------------------------

def verify(run_dir, args):
    state, state_check = load_state(run_dir)
    checks = [state_check, check_wall(state, args.max_wall_s)]
    checks += check_qa(run_dir, args.python, args.skill_dir,
                       args.require_qa_strict, args.skip_qa)
    rollout_check = check_rollout(args.rollout, args.max_model_requests)
    if rollout_check is not None:
        checks.append(rollout_check)
    return checks


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(
        description="Verify one pipeline_driver.py run against its wall-clock "
                    "budget AND re-verify its QA gates from scratch. Speed and "
                    "honesty share one exit code.")
    p.add_argument("run_dir")
    p.add_argument("--max-wall-s", type=float, default=None,
                    help="hard cap on the run's wall clock, in seconds "
                         "(from DRIVER-STATE.json)")
    p.add_argument("--require-qa-strict", action="store_true",
                    help="a run whose QA gates cannot be re-run (no report / no "
                         "datapack) FAILS instead of skipping")
    p.add_argument("--skip-qa", action="store_true",
                    help="budget only: do not re-run qa_check.py (reported loudly)")
    p.add_argument("--rollout", default=None,
                    help="Codex rollout .jsonl to count orchestrator model "
                         "requests from, offline")
    p.add_argument("--max-model-requests", type=int, default=None,
                    help="hard cap on function_call/custom_tool_call events in "
                         "--rollout")
    p.add_argument("--json", action="store_true", help="machine-readable result")
    p.add_argument("--python", default=sys.executable,
                    help="interpreter used to re-run qa_check.py")
    p.add_argument("--skill-dir", default=str(SKILL_DIR),
                    help="skill root holding scripts/qa_check.py")
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        sys.stderr.write(f"verify_run_budget.py: no such run dir: {run_dir}\n")
        return EXIT_BAD_INVOCATION
    if args.skip_qa and args.require_qa_strict:
        sys.stderr.write("verify_run_budget.py: --skip-qa and --require-qa-strict "
                         "are contradictory\n")
        return EXIT_BAD_INVOCATION

    checks = verify(run_dir.resolve(), args)
    failed = [c for c in checks if c.ok is False]
    skipped = [c for c in checks if c.ok is None]
    passed = [c for c in checks if c.ok is True]
    code = EXIT_FAIL if failed else EXIT_OK

    if args.json:
        sys.stdout.write(json.dumps(
            {"run_dir": str(run_dir.resolve()),
             "ok": not failed,
             "exit_code": code,
             "checks": [c.as_dict() for c in checks],
             "summary": {"pass": len(passed), "fail": len(failed),
                          "skip": len(skipped)}}, indent=2) + "\n")
    else:
        out = [f"== RUN BUDGET VERIFY: {run_dir.resolve()} =="]
        for c in checks:
            head, *rest = c.detail.split("\n")
            out.append(f"{c.label} {c.name}: {head}")
            out += rest
        out.append(f"== {len(passed)} pass, {len(failed)} fail, "
                   f"{len(skipped)} skip ==")
        sys.stdout.write("\n".join(out) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
