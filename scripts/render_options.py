#!/usr/bin/env python3
"""Deterministic Dealer-Positioning block: read the P8 options facts from
10-datapack.json and emit a verbatim `52-options-block.md`. The orchestrator
runs this (NOT the writer); the writer inserts the block VERBATIM under the
`## Dealer Positioning & Options` slot and never regenerates it (Invariant 2).

Every scalar fact renders full-digit, comma-separated, with its `[P8.fact]` tag
adjacent so `qa_check.check_pairs` verifies it; ratio facts shown as a percent
ride the `%`+unit:ratio -> /100 rule. Context lists render as tables cited by
tag with NO adjacent number (a number tagged to a list hard-fails check_pairs);
`scan_untagged` skips the whole block via the `options-block` marker. Each fact
carries its daily/snapshot/live history tag (O1/EC5). Stdlib only.

Usage: render_options.py <datapack.json>
Exit 0 ok; 3 when the pack carries no P8 facts and no P8._gaps (nothing to
render — options were not fetched); 2 on bad args."""
import json
import sys

# (fact_id, label, mode). mode drives the number format + the qa unit contract:
#   usd/price -> full-digit "$"; pct -> v*100 with "%" (unit:ratio -> /100 rule);
#   pct_native -> v with "%" (unit already a percent); raw -> bare 4dp ratio;
#   label -> the value IS the text (no number).
SPEC = [
    ("P8.gex_regime", "Gamma regime", "label"),
    ("P8.gex_net", "Net GEX", "usd"),
    ("P8.gex_front_dte", "Front-DTE GEX (0-2d)", "usd"),
    ("P8.iv_rank_1y", "IV rank (1y)", "pct_native"),
    ("P8.iv_now", "Implied vol", "pct"),
    ("P8.rv_now", "Realized vol", "pct"),
    ("P8.iv_cmt_30d", "30d constant-maturity IV", "pct"),
    ("P8.implied_move_front", "Front implied move", "pct"),
    ("P8.rr_skew_25d", "25-delta risk-reversal skew", "pct"),
    ("P8.max_pain_front", "Max pain (front expiry)", "price"),
    ("P8.net_prem_day", "Net premium (day)", "usd"),
    ("P8.pc_ratio_vol", "Put/call volume ratio", "raw"),
    ("P8.net_prem_ticks", "Net premium ticks", "usd"),
    ("P8.nope", "NOPE", "raw"),
    ("P8.spot_gex", "Spot GEX", "usd"),
]

# (list fact_id, header, column labels). Cited by tag, rendered as a table.
LISTS = [
    ("P8.gex_series", "Net GEX daily trend", ["Date", "Net GEX"]),
    ("P8.gex_by_strike", "GEX by strike (walls)", ["Strike", "Net GEX"]),
    ("P8.iv_term", "IV term structure", ["Expiry", "IV"]),
    ("P8.max_pain_by_expiry", "Max pain by expiry", ["Expiry", "Max pain"]),
    ("P8.oi_walls", "Open-interest walls", ["Expiry", "OI", "Volume"]),
    ("P8.flow_alerts", "Unusual flow alerts", ["Type", "Strike", "Expiry", "Volume"]),
    ("P8.smart_flow", "Smart-money flow (scored)",
     ["Score", "Type", "Strike", "Expiry", "Premium", "DTE", "Signals"]),
]

HIST = {"daily": "daily", "snapshot": "snapshot", "live": "live"}
# A cumulative-intraday (live) fact is a partial/absent read in these sessions —
# gate it to DATA-THIN, never a full number (O8/§5 "never a full read").
INCOMPLETE_SESSIONS = {"none", "pre-open"}


def _gated(fact):
    return (fact.get("history") == "live"
            and fact.get("session_state") in INCOMPLETE_SESSIONS)


def money(v):
    """Full-digit, comma-separated, sign-before-$ (PAIR_RE parses either sign pos)."""
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _num(v, mode):
    if mode in ("usd", "price"):
        return money(v)
    if mode == "pct":
        return f"{v * 100:.2f}%"
    if mode == "pct_native":
        return f"{v:.2f}%"  # match uw_options round(v,2); 1dp false-fails qa <0.5% on small iv_rank
    if mode == "raw":
        return f"{v:.4f}"
    return None


def _qual(fact):
    """History tag + any per-fact qualifier (session / event / skew direction)."""
    bits = [HIST.get(fact.get("history"), fact.get("history") or "?")]
    if fact.get("session_state"):
        bits.append(f"session={fact['session_state']}")
    if fact.get("event"):
        bits.append(fact["event"])
    if fact.get("label"):
        bits.append(fact["label"])
    return " · ".join(bits)


def _table(header, rows):
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        cells = [("" if c is None else str(c)) for c in r]
        cells += [""] * (len(header) - len(cells))
        out.append("| " + " | ".join(cells[:len(header)]) + " |")
    return out


def build(pack):
    """Return the verbatim dealer-positioning block. Raise KeyError when the pack
    carries neither a P8 scalar nor a P8._gaps entry (options were never fetched)."""
    scalars = {k: v for k, v in pack.items()
               if k.startswith("P8.") and k != "P8._gaps"
               and isinstance(v, dict) and v.get("unit") != "list"}
    lists = {k: v for k, v in pack.items()
             if isinstance(v, dict) and v.get("unit") == "list"}
    gaps = pack.get("P8._gaps") or []
    if not scalars and not lists and not gaps:
        raise KeyError("no P8 facts")

    lines = ["<!-- options-block: inserted verbatim, do not edit -->",
             "### Dealer positioning & options (computed)"]
    if not scalars and not lists:
        lines.append("- Dealer positioning: DATA GAP — Unusual Whales options data "
                     "unavailable (see gaps below).")
    else:
        for fid, label, mode in SPEC:
            fact = scalars.get(fid)
            if not isinstance(fact, dict) or fact.get("v") is None:
                continue
            if _gated(fact):
                lines.append(f"- {label}: DATA-THIN [{fid}] (live · "
                             f"session={fact.get('session_state')} — cumulative "
                             f"intraday withheld, incomplete session)")
                continue
            q = _qual(fact)
            if mode == "label":
                lines.append(f"- {label}: **{fact['v']}** [{fid}] ({q})")
                continue
            lines.append(f"- {label}: {_num(fact['v'], mode)} [{fid}] ({q})")
        # flip is omitted on short-gamma names — make that explicit, never blank.
        flip = scalars.get("P8.flip_level")
        if isinstance(flip, dict) and flip.get("v") is not None:
            dist = scalars.get("P8.dist_flip")
            tail = (f", {_num(dist['v'], 'pct')} [P8.dist_flip] from spot"
                    if isinstance(dist, dict) and dist.get("v") is not None else "")
            lines.append(f"- Gamma flip: {money(flip['v'])} [P8.flip_level]{tail} "
                         f"({_qual(flip)})")
        elif "P8.gex_net" in scalars:
            lines.append("- Gamma flip: none in range (short-gamma / no net-GEX "
                         "zero-crossing) (snapshot)")
        inc = scalars.get("P8.gex_data_inconsistent")
        if isinstance(inc, dict) and inc.get("v") is True:
            lines.append("- Data-inconsistent [P8.gex_data_inconsistent]: net-GEX "
                         "sign and spot-vs-flip disagree — flip treated as "
                         "unreliable, regime taken from the sign only (snapshot)")
        for side in ("call", "put"):
            w = scalars.get(f"P8.{side}_wall")
            if isinstance(w, dict) and w.get("v") is not None:
                d = scalars.get(f"P8.dist_{side}_wall")
                tail = (f", {_num(d['v'], 'pct')} [P8.dist_{side}_wall] from spot"
                        if isinstance(d, dict) and d.get("v") is not None else "")
                lines.append(f"- {side.capitalize()} wall: {money(w['v'])} "
                             f"[P8.{side}_wall]{tail} ({_qual(w)})")

    for fid, header, cols in LISTS:
        fact = lists.get(fid)
        if not isinstance(fact, dict) or not fact.get("v"):
            continue
        if _gated(fact):  # live flow list in an incomplete session -> withhold
            lines.append("")
            lines.append(f"**{header}** [{fid}] (live · session="
                         f"{fact.get('session_state')}): DATA-THIN — live flow "
                         f"withheld (incomplete session).")
            continue
        rows = fact["v"]
        tail = rows[-8:] if fid == "P8.gex_series" else rows[:12]
        lines.append("")
        lines.append(f"**{header}** [{fid}] ({_qual(fact)}):")
        lines += _table(cols, tail)

    if gaps:
        lines.append("")
        lines.append("**Options data gaps:**")
        for g in gaps:
            lines.append(f"- {g}")

    lines.append("<!-- options-block: end -->")
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: render_options.py <datapack.json>\n")
        return 2
    with open(argv[0]) as fh:
        pack = json.load(fh)
    try:
        block = build(pack)
    except KeyError as e:
        sys.stderr.write(f"ERROR: no options facts to render: {e.args[0]}\n")
        return 3
    sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
