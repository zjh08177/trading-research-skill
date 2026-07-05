#!/usr/bin/env python3
"""Deterministic risk box: compute the adverse move, invalidation anchor, and a
context flag from the data pack, then emit a verbatim block. The risk officer
narrates AROUND this block and never recomputes its numbers (SKILL invariant 16).
The block is context-only — it never states an action or a size, and never
changes the rating. Stdlib only.

Usage: risk_box.py <datapack.json>
Exit 0 ok; 3 on a missing required fact (fail loud, never fabricate); 2 on bad args."""
import json
import sys

REQUIRED = ["P2.atr14", "P2.atr14_pct", "P2.sigma30", "P2.sma50"]
ABNORMAL_ATR = 1.5  # a 1-day move >= this many ATR14 is flagged abnormal (pinned)


def fval(pack, fid):
    """Finite scalar value of a pack fact, else None."""
    f = pack.get(fid)
    if not isinstance(f, dict):
        return None
    v = f.get("v")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def build(pack):
    """Return the verbatim risk-box block. Raise KeyError(names) if a required
    fact (or any price) is absent."""
    price = fval(pack, "P1.last")
    price_tag = "P1.last"
    if price is None:
        price = fval(pack, "P1.price")
        price_tag = "P1.price"
    missing = [f for f in REQUIRED if fval(pack, f) is None]
    if price is None:
        missing.append("P1.last|P1.price")
    if missing:
        raise KeyError(", ".join(missing))

    atr = fval(pack, "P2.atr14")
    atr_pct = fval(pack, "P2.atr14_pct")
    sigma = fval(pack, "P2.sigma30")
    sma50 = fval(pack, "P2.sma50")
    chg = fval(pack, "P1.chg_pct_1d")

    # Today's move in ATR — distinguish the two "cannot compute" causes so the
    # block never makes a false "chg absent" claim (a halted ticker has atr_pct=0).
    if chg is None:
        move_atr = None
        move_line = "- Today's move: DATA GAP (P1.chg_pct_1d absent)"
        gap_reason = "P1.chg_pct_1d absent"
    elif not atr_pct:                       # 0 or absent → cannot normalize
        move_atr = None
        move_line = (f"- Today's move: {chg:.2f}% [P1.chg_pct_1d] — cannot "
                     f"normalize (ATR14% is 0)")
        gap_reason = "ATR14% is 0"
    else:
        move_atr = abs(chg) / atr_pct
        band = ("sub-ATR" if move_atr < 1 else
                ("normal" if move_atr < ABNORMAL_ATR else "ABNORMAL"))
        move_line = (f"- Today's move: {chg:.2f}% [P1.chg_pct_1d] = {move_atr:.2f}× "
                     f"ATR14 ({band})")

    if move_atr is None:                    # no signal → UNKNOWN, never a false NORMAL
        context_line = (f"- Context: UNKNOWN (move n/a — {gap_reason}; "
                        f"context only, not a call)")
    else:
        context = "ABNORMAL" if move_atr >= ABNORMAL_ATR else "NORMAL"
        context_line = (f"- Context: {context} (today {move_atr:.2f}× ATR14 vs "
                        f"{ABNORMAL_ATR:g}× threshold; context only, not a call)")

    def n(x):
        return f"{x:.2f}"                    # fixed-point: no precision loss / sci notation

    lines = [
        "<!-- riskbox-block: inserted verbatim, do not edit -->",
        "### Risk box (computed)",
        move_line,
        (f"- ATR14: {n(atr)} USD [P2.atr14] ({n(atr_pct)}% [P2.atr14_pct] of "
         f"{price_tag} {n(price)}); adverse −1× = {n(price - atr)}, −2× = {n(price - 2 * atr)}"),
        f"- 30d σ: {n(sigma)}% [P2.sigma30]",
        (f"- Invalidation anchor: SMA50 {n(sma50)} [P2.sma50] −1× ATR14 = "
         f"{n(sma50 - atr)} (long) / +1× ATR14 = {n(sma50 + atr)} (short)"),
        context_line,
        "<!-- riskbox-block: end -->",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: risk_box.py <datapack.json>\n")
        return 2
    with open(argv[0]) as f:
        pack = json.load(f)
    try:
        block = build(pack)
    except KeyError as e:
        sys.stderr.write(f"ERROR: risk box needs missing fact(s): {e.args[0]}\n")
        return 3
    sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
