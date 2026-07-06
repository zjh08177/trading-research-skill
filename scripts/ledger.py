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
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REQUIRED = ["run_id", "ticker", "date_utc", "as_of", "job", "mode_rating",
            "distribution", "spread", "no_call", "gaps", "report_path",
            "cost_usd", "wall_s"]

DIRECTION = {"StrongSell": -1, "Sell": -1, "Hold": 0, "Buy": 1, "StrongBuy": 1}
DEFAULT_REGIME = "claude/opus"
SCRIPTS = Path(__file__).resolve().parent


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


def read_jsonl(path, label):
    """Parse a .jsonl file, skipping malformed lines with a visible stderr warning
    (never crash — a truncated trailing line must not brick the read for a whole
    ticker; the aggregate is computed from the valid rows and the skip is surfaced)."""
    rows = []
    if not path.exists():
        return rows
    for i, ln in enumerate(path.read_text().splitlines(), 1):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            sys.stderr.write(f"WARN: skipped malformed {label} line {i} in {path}\n")
    return rows


def add_trading_days(d, n):
    """Add n trading days, skipping weekends only (holidays ignored — the price
    lookup resolves to the last settled bar <= the target date, so a holiday
    landing is corrected there). Approximation, documented."""
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def sidecar_path(main_path):
    """Resolved-outcomes ledger beside the main one: ledger-resolved.jsonl."""
    p = Path(main_path)
    return p.with_name(p.stem + "-resolved" + p.suffix)


def _schwab_close(symbol, iso_date):
    """Default price fn: settled close via schwab_bars.py (upstream venv). Returns
    None on any failure — resolve then skips the row and retries a later run."""
    try:
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "vendors" / "schwab_bars.py"),
             "--ticker", symbol, "--asof", str(iso_date)],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return None
        pack = json.loads(r.stdout)
        v = pack.get("P1.price", {}).get("v")
        return float(v) if isinstance(v, (int, float)) else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def resolve_rows(main_rows, resolved_ids, ticker, horizon, benchmark, asof, price_fn):
    """Pure resolver: for each settled, directional, not-yet-resolved call of
    `ticker`, compute realized return / benchmark-relative alpha / hit. Returns
    (new_rows, price_gap_skips). Never mutates inputs. A settled call whose price
    can't be fetched increments the skip count (surfaced, not silently dropped)
    and is retried on a later run. `hit` = the alpha sign agreed with direction."""
    ticker = ticker.upper()
    new, skipped = [], 0
    for r in main_rows:
        if r.get("ticker", "").upper() != ticker:
            continue
        if r.get("run_id") in resolved_ids or r.get("no_call"):
            continue
        direction = DIRECTION.get(r.get("mode_rating"))
        if not direction:                       # Hold (0) / unknown → not a call
            continue
        rdate = add_trading_days(_day(r["date_utc"]), horizon)
        if rdate > asof:                         # not settled yet
            continue
        entry = price_fn(ticker, r["date_utc"])
        exit_ = price_fn(ticker, rdate.isoformat())
        be = price_fn(benchmark, r["date_utc"])
        bx = price_fn(benchmark, rdate.isoformat())
        if None in (entry, exit_, be, bx) or entry == 0 or be == 0:
            skipped += 1                         # settled but price-gapped — retry later
            continue
        realized = (exit_ - entry) / entry
        bench = (bx - be) / be
        alpha = realized - bench
        new.append({
            "run_id": r["run_id"], "ticker": ticker, "date_utc": r["date_utc"],
            "resolution_date": rdate.isoformat(), "horizon_td": horizon,
            "benchmark": benchmark, "entry_close": entry, "exit_close": exit_,
            "realized_return": realized, "bench_return": bench, "alpha": alpha,
            "mode_rating": r.get("mode_rating"), "direction": direction,
            "hit": (alpha * direction) > 0, "judge_mix": r.get("judge_mix"),
        })
    return new, skipped


def regime_key(judge_mix):
    """Calibration regime for a resolved row's judge panel. Absent/empty judge_mix
    is the legacy all-opus Claude Code panel; otherwise the sorted set of models
    that actually voted (a substituted slot is its own regime, by design)."""
    if not judge_mix:
        return DEFAULT_REGIME
    return "+".join(sorted(set(judge_mix)))


def _calib_line(rows, regime=None):
    n = len(rows)
    hit_rate = 100.0 * sum(1 for r in rows if r.get("hit")) / n
    mean_alpha = 100.0 * sum(r["alpha"] for r in rows) / n
    horizon = rows[-1].get("horizon_td", "?")
    bench = rows[-1].get("benchmark", "SPY")
    label = f" [{regime}]" if regime else ""
    return (f"Resolved calls{label} (N={n}): hit-rate {hit_rate:.0f}% · "
            f"mean alpha {mean_alpha:+.1f}% (vs {bench}, {horizon}td).")


def calibration_footer(main_path, ticker, before):
    """Aggregate hit-rate + mean alpha from the sidecar, look-ahead-guarded on
    resolution_date STRICTLY < `before` (a resolution dated >= as_of would leak
    post-as_of prices). Deterministic neutral aggregate, never per-call. Regime-
    aware: one line per judge_mix regime so a mixed Cursor panel never blends into
    the opus-only rate that feeds P7. A pure-legacy sidecar keeps the original bare
    single line. Returns '' when nothing qualifies."""
    side = sidecar_path(main_path)
    if not side.exists():
        return ""
    ticker = ticker.upper()
    rows = []
    for r in read_jsonl(side, "resolved-ledger"):
        if r.get("ticker", "").upper() != ticker:
            continue
        rd, alpha = r.get("resolution_date"), r.get("alpha")
        if rd is None or alpha is None:
            sys.stderr.write("WARN: skipped resolved row missing "
                             "resolution_date/alpha\n")
            continue
        if _day(rd) < before:                         # strict look-ahead guard
            rows.append(r)
    if not rows:
        return ""
    regimes = {}
    for r in rows:
        regimes.setdefault(regime_key(r.get("judge_mix")), []).append(r)
    if set(regimes) == {DEFAULT_REGIME}:              # legacy-only: unchanged format
        return "\n" + _calib_line(regimes[DEFAULT_REGIME]) + "\n"
    lines = [_calib_line(regimes[k], regime=k) for k in sorted(regimes)]
    return "\n" + "\n".join(lines) + "\n"


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
    for r in read_jsonl(path, "ledger"):
        if r.get("ticker", "").upper() != ticker:
            continue
        du = r.get("date_utc")
        if du and _day(du) < before:                  # look-ahead guard
            rows.append(r)
    if not rows:
        sys.stdout.write(
            f"No prior track record for {ticker} before {args.before}.\n")
        return 0
    out = ["| Date | Rating | Spread | No-call | Report |",
           "|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: r.get("date_utc", "")):
        out.append(f"| {r.get('date_utc', '?')} | {r.get('mode_rating', '?')} "
                   f"| {r.get('spread', '?')} | {r.get('no_call', '?')} "
                   f"| {r.get('report_path', '')} |")
    sys.stdout.write("\n".join(out) + calibration_footer(path, ticker, before))
    return 0


def cmd_resolve(args):
    path = ledger_path(args.ledger)
    asof = _day(args.asof) if args.asof else date.today()
    main_rows = read_jsonl(path, "ledger")
    side = sidecar_path(path)
    resolved_ids = {r.get("run_id") for r in read_jsonl(side, "resolved-ledger")}
    new, skipped = resolve_rows(main_rows, resolved_ids, args.ticker, args.horizon,
                                args.benchmark, asof, _schwab_close)
    if new:
        side.parent.mkdir(parents=True, exist_ok=True)
        with side.open("a") as f:
            for row in new:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    msg = f"{len(new)} resolved for {args.ticker.upper()} (as_of {asof})"
    if skipped:
        msg += f", {skipped} skipped (missing price — will retry)"
    sys.stdout.write(msg + f"; sidecar {side}\n")
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
    rs = sub.add_parser("resolve")
    rs.add_argument("--ticker", required=True)
    rs.add_argument("--horizon", type=int, default=21)
    rs.add_argument("--benchmark", default="SPY")
    rs.add_argument("--asof")
    args = p.parse_args(argv)
    return {"append": cmd_append, "read": cmd_read,
            "resolve": cmd_resolve}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
