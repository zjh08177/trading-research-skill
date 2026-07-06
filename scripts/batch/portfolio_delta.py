#!/usr/bin/env python3
"""Deterministic portfolio delta + adherence (portfolio history B).

Diff two daily holdings snapshots (snapshot_holdings.py envelopes), classify each
symbol's change (New / Exited / Added / Trimmed / basis-restated), and grade each
change against the skill's OWN calls: the ledger rating in effect and the
invalidation monitor's fired triggers (monitor-<date>.json sidecars) in the
(older, newer] window. A fired trigger outranks the standing rating (§3.1
matrix). Zero-LLM, idempotent. Live position data: git-ignored, never
Artifact-published (R4).

Usage: portfolio_delta.py <holdings_dir> <ledger.jsonl> <sidecar_dir> <out_md>
  holdings_dir = reports/portfolio/holdings-history (snapshot_holdings.py output)
  sidecar_dir  = reports/portfolio                  (monitor-<date>.json sidecars)
Exit: 0 ok · 2 usage · 3 <2 snapshots · 5 schema mismatch
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))                    # scripts/batch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # scripts
from ledger import DIRECTION  # noqa: E402  (rating→sign SSOT: Buy/StrongBuy +1, Hold 0, Sell/StrongSell -1)

SCHEMA = 1
EXCLUDE_SYMBOLS = {"O92E", "TG3Y", "PS"}          # cash-sweep / MMF junk, pinned
SNAP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
SIDECAR_RE = re.compile(r"^monitor-(\d{4}-\d{2}-\d{2})\.json$")


# ---- float-jitter tolerance (cross-account sum order is not stable day-to-day) ----

def _tol(a, b):
    return max(1e-9, 1e-6 * max(abs(a), abs(b)))


def _changed(a, b):
    return abs(b - a) > _tol(a, b)


def _num(row, key):
    v = (row or {}).get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- snapshot loading ----

def select_snapshots(holdings_dir):
    """Ascending YYYY-MM-DD dates of files matching the snapshot regex (tmp and
    .icloud placeholders excluded by the regex)."""
    if not os.path.isdir(holdings_dir):
        return []
    return sorted(f[:-5] for f in os.listdir(holdings_dir) if SNAP_RE.match(f))


def load_snapshot(holdings_dir, date):
    return json.load(open(os.path.join(holdings_dir, date + ".json")))


def vendor_of(env):
    """The verbatim holdings payload — envelope's `vendor`, or a raw dump as-is."""
    return env.get("vendor", env)


def _excluded(row):
    return row.get("kind") == "mutualfund" or row.get("symbol") in EXCLUDE_SYMBOLS


def holdings_by_symbol(vendor):
    out = {}
    for h in (vendor or {}).get("holdings", []):
        s = h.get("symbol")
        if s and not _excluded(h):
            out[s] = h
    return out


# ---- trigger direction (free-text monitor action → sign) ----

def trigger_dir(action):
    """Add→+1; Trim/Sell/Exit→−1; Stop trimming / re-rate→0 (informational);
    unknown free-text → None (no directional trigger)."""
    a = (action or "").strip().lower()
    if not a:
        return None
    if a.startswith("stop") or "re-rate" in a or "rerate" in a:
        return 0
    if a.startswith("add"):
        return 1
    if a.startswith(("trim", "sell", "exit")):
        return -1
    return None


def load_sidecars(sidecar_dir, older, newer):
    """Fired triggers from monitor-<date>.json with older < date ≤ newer, each
    tagged with its file date. Pre-sidecar days simply contribute nothing."""
    rows = []
    if not os.path.isdir(sidecar_dir):
        return rows
    for f in sorted(os.listdir(sidecar_dir)):
        m = SIDECAR_RE.match(f)
        if not m or not (older < m.group(1) <= newer):
            continue
        try:
            data = json.load(open(os.path.join(sidecar_dir, f)))
        except (OSError, ValueError):
            continue
        for r in data or []:
            rows.append(dict(r, date=m.group(1)))
    return rows


# ---- rating axis (ledger call in effect) ----

def _d10(s):
    return str(s or "")[:10]


def _row_date(r):
    """Ledger row's effective date, first 10 chars; date_utc may be bare date."""
    return _d10(r.get("as_of") or r.get("date_utc"))


def rating_axis(ledger_rows, symbol, older, newer):
    """(rating_dir, in_window, as_of_used). A call updated in (older, newer] beats
    the standing baseline (as_of ≤ older); a no_call row carries no direction."""
    sym = symbol.upper()
    rows = [r for r in ledger_rows if str(r.get("ticker", "")).upper() == sym]
    window = [r for r in rows if older < _row_date(r) <= newer]
    baseline = [r for r in rows if _row_date(r) <= older]
    chosen, in_window = None, False
    if window:
        chosen, in_window = max(window, key=_row_date), True
    elif baseline:
        chosen = max(baseline, key=_row_date)
    if chosen is None:
        return None, False, None
    if chosen.get("no_call"):
        return None, in_window, _row_date(chosen)
    return DIRECTION.get(chosen.get("mode_rating")), in_window, _row_date(chosen)


# ---- §3.1 verdict matrix (trigger outranks rating) ----

def verdict(action_dir, triggers, rating_dir):
    """Returns (verdict, axis, cited_triggers). triggers carry `tdir`."""
    tdirs = {t.get("tdir") for t in triggers if t.get("tdir") in (1, -1)}
    if len(tdirs) > 1:
        return "mixed", "trigger", triggers                 # conflicting → list every fired trigger
    if len(tdirs) == 1:
        base, axis = next(iter(tdirs)), "trigger"
        cited = [t for t in triggers if t.get("tdir") in (1, -1)]
    elif rating_dir in (1, -1):
        base, axis, cited = rating_dir, "rating", []
    else:
        return "no-call", None, []
    return ("followed" if action_dir == base else "against"), axis, cited


# ---- change classification ----

def _basis_restated(o, n):
    co, cn = _num(o, "avg_cost"), _num(n, "avg_cost")
    if co is None and cn is None:
        return False
    if (co is None) != (cn is None):
        return True                                          # basis appeared or disappeared
    return _changed(co, cn)


def _classify(sym, o, n):
    """One symbol → a change dict, or None when qty is unchanged and basis intact."""
    kind = (n or o).get("kind")
    if o is None:
        return {"symbol": sym, "kind": kind, "change": "New", "action_dir": 1,
                "q_old": 0.0, "q_new": _num(n, "qty") or 0.0,
                "value_delta": _num(n, "market_value") or 0.0, "basis_restated": False}
    if n is None:
        return {"symbol": sym, "kind": kind, "change": "Exited", "action_dir": -1,
                "q_old": _num(o, "qty") or 0.0, "q_new": 0.0,
                "value_delta": -(_num(o, "market_value") or 0.0), "basis_restated": False}
    qo, qn = _num(o, "qty") or 0.0, _num(n, "qty") or 0.0
    if _changed(qo, qn):
        dq, price = qn - qo, _num(n, "price")
        return {"symbol": sym, "kind": kind,
                "change": "Added" if dq > 0 else "Trimmed",
                "action_dir": 1 if dq > 0 else -1, "q_old": qo, "q_new": qn, "dq": dq,
                "value_delta": (dq * price) if price is not None else None,
                "basis_restated": _basis_restated(o, n)}
    if _basis_restated(o, n):
        return {"symbol": sym, "kind": kind, "change": "basis-restated",
                "action_dir": None, "q_old": qo, "q_new": qn, "value_delta": None,
                "basis_restated": True,
                "avg_cost_old": _num(o, "avg_cost"), "avg_cost_new": _num(n, "avg_cost")}
    return None                                              # unchanged qty, basis intact → omit


def build_report(older, newer, old_env, new_env, ledger_rows, sidecars):
    old_v, new_v = vendor_of(old_env), vendor_of(new_env)
    old_skipped = int((old_v or {}).get("accounts_skipped") or 0)
    new_skipped = int((new_v or {}).get("accounts_skipped") or 0)
    partial = old_skipped > 0 or new_skipped > 0
    skipped_n = max(old_skipped, new_skipped)
    old, new = holdings_by_symbol(old_v), holdings_by_symbol(new_v)

    changes = []
    for sym in sorted(set(old) | set(new)):
        c = _classify(sym, old.get(sym), new.get(sym))
        if c is None:
            continue
        trigs = [dict(t, tdir=trigger_dir(t.get("action"))) for t in sidecars
                 if str(t.get("ticker", "")).upper() == sym.upper()]
        if c["change"] == "basis-restated":
            c.update(verdict=None, axis=None, triggers=trigs)
        elif c["action_dir"] == -1 and partial:
            # a skipped account can masquerade as a sell — never grade a phantom trim
            c.update(verdict="unverifiable", axis=None, triggers=trigs,
                     note=f"partial snapshot ({skipped_n} accounts skipped)")
        else:
            rdir, in_window, rasof = rating_axis(ledger_rows, sym, older, newer)
            v, axis, cited = verdict(c["action_dir"], trigs, rdir)
            c.update(verdict=v, axis=axis, triggers=cited, rating_dir=rdir,
                     rating_asof=rasof, rating_in_window=in_window)
        changes.append(c)

    return {"older_date": older, "newer_date": newer, "old_skipped": old_skipped,
            "new_skipped": new_skipped, "partial": partial, "skipped_n": skipped_n,
            "gap_days": _gap_days(older, newer), "changes": changes}


def _gap_days(older, newer):
    try:
        return (datetime.date.fromisoformat(newer) - datetime.date.fromisoformat(older)).days
    except (ValueError, TypeError):
        return None


# ---- rendering ----

def _g(x):
    return "—" if x is None else f"{x:g}"


def _chg_label(c):
    tail = " *(basis restated)*" if c.get("basis_restated") and c["change"] != "basis-restated" else ""
    return c["change"] + tail


def _val_cell(c):
    v = c.get("value_delta")
    return "—" if v is None else f"${v:+,.0f}"


def _verdict_cell(c):
    v = c.get("verdict")
    if v is None:
        return "—"
    if v == "unverifiable":
        return f"unverifiable — {c.get('note', 'partial snapshot')}"
    if v == "mixed":
        return "mixed — conflicting triggers"
    if c.get("axis") == "rating" and c.get("rating_in_window"):
        return f"{v} (rating, call updated in window)"
    return f"{v} ({c['axis']})" if c.get("axis") else v


def _adherence_line(c):
    sym = c["symbol"]
    if c["change"] == "basis-restated":
        return (f"- **{sym}**: avg_cost restated {_g(c.get('avg_cost_old'))} → "
                f"{_g(c.get('avg_cost_new'))} on unchanged qty — flagged, not graded.")
    bits = [f"- **{sym}** {c['change'].lower()}"]
    trigs = c.get("triggers") or []
    if trigs:
        bits.append("triggers " + "; ".join(
            f"{t.get('action', '?')} (fired {t.get('date', '?')})" for t in trigs))
    if c.get("axis") == "rating" and c.get("rating_asof"):
        w = ", updated in window" if c.get("rating_in_window") else ""
        bits.append(f"rating dir {c.get('rating_dir')} as_of {c.get('rating_asof')}{w}")
    v = c.get("verdict") or "—"
    bits.append(f"verdict **{'unverifiable (partial snapshot)' if v == 'unverifiable' else v}**")
    return " · ".join(bits)


def render_md(report):
    older, newer, changes = report["older_date"], report["newer_date"], report["changes"]
    graded = [c for c in changes if c.get("verdict") in ("followed", "against", "mixed")]
    followed = sum(1 for c in graded if c["verdict"] == "followed")

    head = f"**{len(changes)}** changed position(s)"
    if (report.get("gap_days") or 0) > 1:
        head += (f" · **{report['gap_days']}-day gap** between snapshots (missing days — "
                 "possible iCloud eviction or a skipped run)")
    if report["partial"]:
        head += (f" · PARTIAL book ({report['skipped_n']} accounts skipped) — "
                 "trim/exit verdicts suppressed")
    if graded:
        head += f" · adherence {followed}/{len(graded)} followed"

    L = [f"# Portfolio delta — {newer} (vs {older})", "", head + ".", ""]
    if not changes:
        L += ["**No composition change** — every held position unchanged in qty.", ""]
    else:
        L += ["## Changes", "",
              "| Symbol | Kind | Change | Qty old→new | Δ value | Verdict |",
              "|---|---|---|---|---|---|"]
        for c in changes:
            L.append(f"| {c['symbol']} | {c['kind'] or '?'} | {_chg_label(c)} | "
                     f"{_g(c['q_old'])} → {_g(c['q_new'])} | {_val_cell(c)} | {_verdict_cell(c)} |")
        L += ["", "## Adherence detail", ""]
        L += [_adherence_line(c) for c in changes]
        L.append("")

    L += ["## Provenance & caveats", "",
          "- Deterministic diff of two holdings snapshots; **no LLM ran**. Decision "
          "support only; not financial advice.",
          f"- Rating axis = the ledger call in effect (as_of ≤ {older}); a call updated in "
          f"({older}, {newer}] is labeled and counts as followed-if-matched.",
          "- Trigger axis = fired monitor triggers in the window; a fired trigger outranks "
          "the standing rating.",
          "- Qty tolerance max(1e-9, 1e-6·max|q|) absorbs cross-account float-sum jitter; "
          "avg_cost restatements are flagged, never interpreted."]
    if report["partial"]:
        L.append("- Partial snapshot: an account was skipped, so Exited/Trimmed rows are "
                 "unverifiable (a skipped account can masquerade as a sell) and are NOT graded.")
    L.append("")
    return "\n".join(L)


# ---- driver ----

def _read_ledger(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for ln in open(path):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return rows


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 4:
        sys.stderr.write("usage: portfolio_delta.py <holdings_dir> <ledger.jsonl> "
                         "<sidecar_dir> <out_md>\n")
        return 2
    holdings_dir, ledger_p, sidecar_dir, out_md = argv[:4]
    dates = select_snapshots(holdings_dir)
    if len(dates) < 2:
        sys.stderr.write(f"need ≥2 snapshots to diff; history starts "
                         f"{dates[0] if dates else '—'}\n")
        return 3
    older, newer = dates[-2], dates[-1]
    old_env, new_env = load_snapshot(holdings_dir, older), load_snapshot(holdings_dir, newer)
    for env, d in ((old_env, older), (new_env, newer)):
        if env.get("schema") != SCHEMA:
            sys.stderr.write(f"schema mismatch in {d}.json (got {env.get('schema')!r}, "
                             f"expected {SCHEMA})\n")
            return 5
    report = build_report(older, newer, old_env, new_env,
                          _read_ledger(ledger_p), load_sidecars(sidecar_dir, older, newer))
    md = render_md(report)
    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    open(out_md, "w").write(md)
    with open(os.path.splitext(out_md)[0] + ".json", "w") as f:
        json.dump(report, f, indent=2)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
