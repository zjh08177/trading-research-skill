#!/usr/bin/env python3
"""Daily holdings snapshot — the day's single holdings SSOT (portfolio history A).

Subprocess the SnapTrade holdings CLI (NEVER import it: the CLI binds
``engine.cli`` + the SnapTrade SDK, both resident only in the quant-engine venv;
a byte-copy would break at import — see project-uw-common-drift-self-contained),
wrap its stdout verbatim in a dated envelope, and write one file per day to
``reports/portfolio/holdings-history/YYYY-MM-DD.json``. The monitor and
action-plan then read this file instead of each fetching live, so the book is
pulled once per day and same-day artifacts can never disagree. Zero-LLM.

The written JSON is live position data: it is git-ignored in the vault and never
Artifact-published (R4).

Usage: snapshot_holdings.py <holdings_history_dir> [asof_date]
Env (vendor invocation, matching the skill-venv convention):
  SNAPTRADE_HOLDINGS_PY   skill venv python (default pinned below)
  SNAPTRADE_HOLDINGS_CLI  holdings CLI path (default pinned below)
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

SCHEMA = 1
DEFAULT_PY = "/Users/bytedance/.claude/skills/trading-research/.venv/bin/python"
DEFAULT_CLI = ("/Users/bytedance/.claude/skills/trading-research/"
               "scripts/vendors/snaptrade_holdings.py")


def build_envelope(vendor, asof_date, fetched_at):
    """Wrap the holdings CLI stdout (already parsed) verbatim + complete. Pure."""
    return {"kind": "holdings-snapshot", "schema": SCHEMA, "asof_date": asof_date,
            "fetched_at": fetched_at, "vendor": vendor}


def accounts_skipped(vendor):
    """Partial-book indicator from the vendor payload (0 when clean/absent)."""
    return int((vendor or {}).get("accounts_skipped") or 0)


def no_downgrade_ok(out_dir, asof_date, new_skipped):
    """A same-day rerun must never REPLACE a cleaner book with a more-partial one
    (broker flaps skip accounts). Returns (ok_to_write, existing_skipped_or_None)."""
    path = os.path.join(out_dir, asof_date + ".json")
    if not os.path.exists(path):
        return True, None
    try:
        existing = json.load(open(path))
    except (OSError, ValueError):
        return True, None                      # unreadable existing → overwrite clean
    ex = accounts_skipped(existing.get("vendor"))
    return new_skipped <= ex, ex


def write_atomic(out_dir, asof_date, envelope):
    """iCloud-safe write: same-dir dot-prefixed tmp + os.replace (a cross-device
    rename fails EXDEV; the tmp name must not match the snapshot selection regex
    ``^\\d{4}-\\d{2}-\\d{2}\\.json$``, so it is dot-prefixed and .tmp-suffixed)."""
    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, asof_date + ".json")
    tmp = os.path.join(out_dir, "." + asof_date + ".json.tmp")
    with open(tmp, "w") as f:
        json.dump(envelope, f, separators=(",", ":"))
    os.replace(tmp, final)
    return final


def _default_runner():
    """Subprocess the quant-engine holdings CLI. Returns (returncode, stdout)."""
    py = os.environ.get("SNAPTRADE_HOLDINGS_PY", DEFAULT_PY)
    cli = os.environ.get("SNAPTRADE_HOLDINGS_CLI", DEFAULT_CLI)
    r = subprocess.run([py, cli], capture_output=True, text=True, timeout=120)
    if r.stderr.strip():
        sys.stderr.write(r.stderr if r.stderr.endswith("\n") else r.stderr + "\n")
    return r.returncode, r.stdout


def _parse_args(argv):
    """Consume the CLI BEFORE any positional binding or vendor call, so an
    option-looking token can never become the output directory: a routine
    ``--help`` probe once bound ``--help`` as <holdings_history_dir>, performed a
    LIVE SnapTrade fetch, and wrote real position JSON into ``./--help/`` in an
    ungitignored tree (R4 near-miss). Returns (args, exit_code); exit_code is not
    None when argparse already handled the invocation (``-h`` → 0, bad flag → 2).
    Positional invocation is unchanged — build_datapack.py and SKILL.md call it
    as ``snapshot_holdings.py <dir> [asof]``."""
    parser = argparse.ArgumentParser(
        prog="snapshot_holdings.py",
        description="Write the day's holdings snapshot (the holdings SSOT) to "
                    "<holdings_history_dir>/YYYY-MM-DD.json.")
    parser.add_argument("holdings_history_dir",
                        help="output dir, e.g. reports/portfolio/holdings-history")
    parser.add_argument("asof_date", nargs="?", default=None,
                        help="YYYY-MM-DD (default: today)")
    try:
        return parser.parse_args(argv), None
    except SystemExit as e:                    # -h/--help or an unparseable flag
        return None, int(e.code or 0)


def main(argv=None, runner=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.stderr.write("usage: snapshot_holdings.py <holdings_history_dir> [asof_date]\n")
        return 1
    args, rc = _parse_args(argv)
    if rc is not None:                         # nothing fetched, nothing written
        return rc
    out_dir = args.holdings_history_dir
    asof_date = args.asof_date or datetime.date.today().isoformat()
    runner = runner or _default_runner

    rc, stdout = runner()
    if rc != 0:                                # pass the vendor family code through;
        sys.stderr.write(f"holdings CLI failed (exit {rc}); nothing written "
                         "(2=auth/re-link, 3=no-data, 4=rate-limit)\n")
        return rc if rc in (2, 3, 4) else 1    # wrapper's own failures collapse to 1
    stdout = (stdout or "").strip()
    if not stdout:
        sys.stderr.write("holdings CLI produced no output; nothing written\n")
        return 1
    try:
        vendor = json.loads(stdout)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"holdings CLI output not JSON ({e}); nothing written\n")
        return 1

    skipped = accounts_skipped(vendor)
    ok, ex_skipped = no_downgrade_ok(out_dir, asof_date, skipped)
    if not ok:
        sys.stderr.write(f"kept existing {asof_date}.json (accounts_skipped {ex_skipped} "
                         f"< this run's {skipped}); refusing to downgrade the book\n")
        return 0
    if skipped > 0:
        sys.stderr.write(f"DEGRADED: partial book — {skipped} account(s) skipped in the "
                         f"{asof_date} snapshot; delta will gate its trim/exit verdicts\n")
    envelope = build_envelope(
        vendor, asof_date, datetime.datetime.now(datetime.timezone.utc).isoformat())
    path = write_atomic(out_dir, asof_date, envelope)
    sys.stdout.write(f"wrote {path} (accounts_skipped={skipped})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
