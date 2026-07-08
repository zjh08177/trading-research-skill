#!/usr/bin/env python3
"""Metadata-only usage ledger for the trading-research skill.

The ledger is local-only, append-only JSONL. It records that a run happened and
where its artifacts live; it never stores report bodies, vote text, holdings, or
position/cost amounts.
"""
import argparse
import fcntl
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SKILL = "trading-research"
ALLOWED_HOSTS = {"claude-code", "codex", "cursor", "unknown"}
DENYLIST = (
    "qty",
    "quantity",
    "shares",
    "share_count",
    "holdings",
    "holding",
    "market_value",
    "avg_cost",
    "cost_usd",
    "unrealized",
    "pnl",
    "p&l",
    "dollar",
    "amount",
)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def detect_host(env=None, cwd=None):
    env = env or os.environ
    cwd = str(cwd or os.getcwd())
    override = env.get("TRADING_RESEARCH_HOST")
    if override in {"claude-code", "codex", "cursor"}:
        return override, ["explicit_TRADING_RESEARCH_HOST"]

    codex_keys = [k for k in ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_MANAGED_BY_NPM") if env.get(k)]
    if codex_keys:
        signals = codex_keys[:]
        if any(env.get(k) for k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_JOB_DIR")):
            signals.append("nested_from_claude")
        return "codex", signals

    cursor_keys = [k for k in env if k.startswith("CURSOR_")]
    if env.get("TERM_PROGRAM") == "Cursor":
        cursor_keys.append("TERM_PROGRAM=Cursor")
    if cursor_keys:
        return "cursor", sorted(cursor_keys)

    claude_keys = [k for k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_JOB_DIR") if env.get(k)]
    if claude_keys:
        return "claude-code", claude_keys

    if "/.cursor/" in cwd:
        return "cursor", ["cwd:.cursor"]
    if "/.codex/" in cwd:
        return "codex", ["cwd:.codex"]
    if "/.claude/jobs/" in cwd:
        return "claude-code", ["cwd:.claude/jobs"]
    return "unknown", []


def usage_path(env=None):
    env = env or os.environ
    if "TRADING_RESEARCH_USAGE_LEDGER" in env:
        raw = env.get("TRADING_RESEARCH_USAGE_LEDGER", "")
        if raw == "":
            sys.stderr.write("ERROR: TRADING_RESEARCH_USAGE_LEDGER is empty\n")
            raise SystemExit(2)
        return Path(raw).expanduser()
    data_home = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data_home).expanduser() / "trading-research" / "usage" / "invocations.jsonl"


def _bad_key(key):
    low = key.lower()
    if low == "position_aware":
        return None
    for token in DENYLIST:
        if token in low:
            return key
    return None


def validate_meta(obj):
    if not isinstance(obj, dict):
        raise ValueError("meta-json must decode to an object")
    stack = list(obj.items())
    while stack:
        key, value = stack.pop()
        bad = _bad_key(str(key))
        if bad:
            raise ValueError(f"denylisted usage metadata key: {bad}")
        if isinstance(value, dict):
            stack.extend(value.items())
    return obj


def locked_append(path, row):
    line = json.dumps(row, ensure_ascii=False, sort_keys=True)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
    except OSError as e:
        sys.stderr.write(f"=== MANUAL-USAGE-APPEND REQUIRED ({e}) ===\n{line}\n")
        raise SystemExit(2)


def _normalize_job_tier(raw):
    if not raw:
        return None
    m = re.search(r"\bJ\d+\b", raw.upper())
    return m.group(0) if m else raw


def _split_csv(raw):
    if not raw:
        return None
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _parse_meta(raw):
    if not raw:
        return {}
    try:
        return validate_meta(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as e:
        sys.stderr.write(f"ERROR: {e}\n")
        raise SystemExit(2)


def build_row(event, args, env=None):
    env = env or os.environ
    host, signals = detect_host(env, args.cwd)
    source = "claude-hook" if event == "host_hook" else "skill-helper"
    if event == "start":
        invocation_id = args.invocation_id or str(uuid.uuid4())
    else:
        invocation_id = args.invocation_id or env.get("TRADING_RESEARCH_INVOCATION_ID")
    if event in {"end", "fail"} and not invocation_id:
        sys.stderr.write("ERROR: missing invocation id (pass --invocation-id or env)\n")
        raise SystemExit(2)
    if event == "host_hook" and not invocation_id:
        invocation_id = f"claude:{env.get('CLAUDE_CODE_SESSION_ID', 'unknown')}:{uuid.uuid4()}"

    status = args.status
    if not status:
        status = {"start": "started", "end": "success", "fail": "failed",
                  "host_hook": "hook_only"}[event]
    report_paths = list(args.report_path or [])
    child_run_ids = list(args.child_run_id or [])
    tickers = _split_csv(args.tickers)
    ticker = args.ticker.upper() if args.ticker else None

    row = {
        "v": 1,
        "event": event,
        "invocation_id": invocation_id,
        "batch_id": args.batch_id,
        "ts": args.ts or utc_now(),
        "host": host,
        "host_signals": signals,
        "source": source,
        "session_id": env.get("CLAUDE_CODE_SESSION_ID") or env.get("CODEX_THREAD_ID"),
        "cwd": args.cwd or os.getcwd(),
        "skill": SKILL,
        "mode": args.mode,
        "ticker": ticker,
        "tickers": tickers,
        "job_tier": _normalize_job_tier(args.job_tier),
        "position_aware": args.position_aware,
        "asset_class": args.asset_class,
        "run_id": args.run_id,
        "run_dir": args.run_dir,
        "report_paths": report_paths,
        "child_run_ids": child_run_ids or None,
        "join_status": None,
        "status": status,
        "exit_code": args.exit_code,
        "wall_s": args.wall_s,
        "meta": _parse_meta(args.meta_json),
    }
    return {k: v for k, v in row.items() if v is not None and v != []}


def _add_common(p):
    p.add_argument("--invocation-id")
    p.add_argument("--batch-id")
    p.add_argument("--ts")
    p.add_argument("--cwd")
    p.add_argument("--mode")
    p.add_argument("--ticker")
    p.add_argument("--tickers")
    p.add_argument("--job-tier")
    p.add_argument("--position-aware", action="store_const", const=True, default=None)
    p.add_argument("--asset-class")
    p.add_argument("--run-id")
    p.add_argument("--run-dir")
    p.add_argument("--report-path", action="append")
    p.add_argument("--child-run-id", action="append")
    p.add_argument("--status")
    p.add_argument("--exit-code", type=int)
    p.add_argument("--wall-s", type=float)
    p.add_argument("--meta-json")


def cmd_detect(args):
    host, signals = detect_host(os.environ, os.getcwd())
    if args.json:
        sys.stdout.write(json.dumps({"host": host, "host_signals": signals}) + "\n")
    else:
        sys.stdout.write(f"{host}\n")
    return 0


def cmd_event(name, args):
    path = usage_path()
    row = build_row(name, args)
    locked_append(path, row)
    if name == "start":
        sys.stdout.write(f"export TRADING_RESEARCH_INVOCATION_ID={row['invocation_id']}\n")
    else:
        sys.stdout.write(f"{name}: {row['invocation_id']}\n")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect-host")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_detect)
    for name in ("start", "end", "fail", "host-hook"):
        sp = sub.add_parser(name)
        _add_common(sp)
        sp.set_defaults(fn=lambda args, n=name.replace("-", "_"): cmd_event(n, args))
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
